import asyncio
import logging
import time

from tornado import web

from ..options import options
from ..utils.broker import Broker
from ..views import BaseHandler

logger = logging.getLogger(__name__)


class WorkerView(BaseHandler):
    @web.authenticated
    async def get(self, name):
        try:
            self.application.update_workers(workername=name)
        except Exception as e:
            logger.error(e)

        worker = self.application.workers.get(name)

        if worker is None:
            raise web.HTTPError(404, f"Unknown worker '{name}'")
        if 'stats' not in worker:
            raise web.HTTPError(404, f"Unable to get stats for '{name}' worker")

        self.render("worker.html", worker=dict(worker, name=name))


class WorkersView(BaseHandler):
    @web.authenticated
    async def get(self):
        refresh = self.get_argument('refresh', default=False, type=bool)
        json = self.get_argument('json', default=False, type=bool)

        events = self.application.events.state

        if refresh:
            try:
                await asyncio.wait(self.application.update_workers())
            except Exception as e:
                logger.exception('Failed to update workers: %s', e)

        workers = {}
        for name, values in events.counter.items():
            if name not in events.workers:
                continue
            worker = events.workers[name]
            info = dict(values)
            info.update(self._as_dict(worker))
            info.update(status=worker.alive)
            info.update(pending=self._pending_tasks_count(name))
            workers[name] = info

        if options.purge_offline_workers is not None:
            timestamp = int(time.time())
            offline_workers = []
            for name, info in workers.items():
                if info.get('status', True):
                    continue

                heartbeats = info.get('heartbeats', [])
                last_heartbeat = int(max(heartbeats)) if heartbeats else None
                if not last_heartbeat or timestamp - last_heartbeat > options.purge_offline_workers:
                    offline_workers.append(name)

            for name in offline_workers:
                workers.pop(name)

        queues = await self._queue_lengths()
        queue_breakdown = self._queue_breakdown(queues)
        queued_tasks = sum(queue['queued'] for queue in queue_breakdown)

        if json:
            self.write(dict(data=list(workers.values()),
                            active_queues=queues,
                            queued_tasks=queued_tasks,
                            queue_breakdown=queue_breakdown))
        else:
            self.render("workers.html",
                        workers=workers,
                        active_queues=queues,
                        queued_tasks=queued_tasks,
                        queue_breakdown=queue_breakdown,
                        broker=self.application.capp.connection().as_uri(),
                        autorefresh=1 if self.application.options.auto_refresh else 0)

    def _pending_tasks_count(self, workername):
        worker = self.application.workers.get(workername, {})
        reserved = worker.get('reserved', []) or []
        scheduled = worker.get('scheduled', []) or []
        return len(reserved) + len(scheduled)

    def _queue_breakdown(self, queues):
        default_queue = self.capp.conf.task_default_queue
        queue_names = {queue.get('name') for queue in queues if queue.get('name')}
        queue_names.update(self.get_active_queue_names())
        breakdown = {
            name: {
                'name': name,
                'queued': 0,
                'reserved_scheduled': 0,
                'failed': 0,
                'active': 0,
            }
            for name in queue_names
        }

        for queue in queues:
            name = queue.get('name')
            if not name:
                continue
            breakdown.setdefault(name, {
                'name': name,
                'queued': 0,
                'reserved_scheduled': 0,
                'failed': 0,
                'active': 0,
            })['queued'] = self._messages_count(queue)

        for worker in self.application.workers.values():
            for task in worker.get('active', []) or []:
                self._increment_queue_count(
                    breakdown, self._task_queue(task, default_queue), 'active')
            for task in worker.get('reserved', []) or []:
                self._increment_queue_count(
                    breakdown, self._task_queue(task, default_queue),
                    'reserved_scheduled')
            for task in worker.get('scheduled', []) or []:
                self._increment_queue_count(
                    breakdown, self._task_queue(task, default_queue),
                    'reserved_scheduled')

        for task in self.application.events.state.tasks.values():
            state = getattr(task, 'state', None)
            if state == 'FAILURE':
                self._increment_queue_count(
                    breakdown, self._task_queue(task, default_queue), 'failed')

        return sorted(breakdown.values(), key=lambda queue: queue['name'])

    @staticmethod
    def _increment_queue_count(breakdown, queue, key):
        if not queue:
            queue = 'unknown'
        breakdown.setdefault(queue, {
            'name': queue,
            'queued': 0,
            'reserved_scheduled': 0,
            'failed': 0,
            'active': 0,
        })[key] += 1

    @classmethod
    def _task_queue(cls, task, default_queue=None):
        if isinstance(task, dict):
            request = task.get('request')
            if isinstance(request, dict):
                queue = cls._task_queue(request)
                if queue:
                    return queue
            delivery_info = task.get('delivery_info') or {}
            if isinstance(delivery_info, dict):
                queue = delivery_info.get('queue') or delivery_info.get('routing_key')
                if queue:
                    return queue
            return task.get('queue') or task.get('routing_key') or default_queue

        delivery_info = getattr(task, 'delivery_info', None) or {}
        if isinstance(delivery_info, dict):
            queue = delivery_info.get('queue') or delivery_info.get('routing_key')
            if queue:
                return queue
        return (getattr(task, 'queue', None) or
                getattr(task, 'routing_key', None) or
                default_queue)

    @staticmethod
    def _messages_count(queue):
        try:
            return int(queue.get('messages', 0) or 0)
        except (TypeError, ValueError):
            return 0

    async def _queue_lengths(self):
        http_api = None
        app = self.application
        if app.transport == 'amqp' and app.options.broker_api:
            http_api = app.options.broker_api

        broker = Broker(app.capp.connection().as_uri(include_password=True),
                        http_api=http_api,
                        broker_options=self.capp.conf.broker_transport_options,
                        broker_use_ssl=self.capp.conf.broker_use_ssl)
        try:
            return await broker.queues(self.get_active_queue_names())
        except Exception as e:
            logger.exception('Failed to get queue lengths: %s', e)
            return []

    @classmethod
    def _as_dict(cls, worker):
        if hasattr(worker, '_fields'):
            return dict((k, getattr(worker, k)) for k in worker._fields)
        return cls._info(worker)

    @classmethod
    def _info(cls, worker):
        _fields = ('hostname', 'pid', 'freq', 'heartbeats', 'clock',
                   'active', 'processed', 'loadavg', 'sw_ident',
                   'sw_ver', 'sw_sys')

        def _keys():
            for key in _fields:
                value = getattr(worker, key, None)
                if value is not None:
                    yield key, value

        return dict(_keys())
