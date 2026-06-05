import asyncio
import logging

from tornado import web

from ..views import BaseHandler

logger = logging.getLogger(__name__)


class HomeView(BaseHandler):
    @web.authenticated
    async def get(self):
        try:
            await asyncio.wait(self.application.update_workers())
        except Exception as e:
            logger.exception('Failed to update workers: %s', e)

        overview = self._overview()
        self.render("home.html", overview=overview)

    def _overview(self):
        events = self.application.events.state
        worker_names = set(events.workers.keys()) | set(self.application.workers.keys())
        workers = []

        totals = {
            'workers': len(worker_names),
            'online_workers': 0,
            'running': 0,
            'reserved': 0,
            'scheduled': 0,
            'pending': 0,
            'processed': 0,
            'failed': 0,
            'succeeded': 0,
            'retried': 0,
        }

        for name in sorted(worker_names):
            event_worker = events.workers.get(name)
            inspected_worker = self.application.workers.get(name, {})
            counters = events.counter.get(name, {})

            running = self._running_tasks_count(event_worker, inspected_worker)
            reserved = len(inspected_worker.get('reserved', []) or [])
            scheduled = len(inspected_worker.get('scheduled', []) or [])
            pending = reserved + scheduled
            status = bool(event_worker and event_worker.alive)

            row = {
                'name': name,
                'status': status,
                'running': running,
                'reserved': reserved,
                'scheduled': scheduled,
                'pending': pending,
                'processed': counters.get('task-received', 0),
                'failed': counters.get('task-failed', 0),
                'succeeded': counters.get('task-succeeded', 0),
                'retried': counters.get('task-retried', 0),
            }
            workers.append(row)

            totals['online_workers'] += 1 if status else 0
            totals['running'] += running
            totals['reserved'] += reserved
            totals['scheduled'] += scheduled
            totals['pending'] += pending
            totals['processed'] += row['processed']
            totals['failed'] += row['failed']
            totals['succeeded'] += row['succeeded']
            totals['retried'] += row['retried']

        return {
            'totals': totals,
            'workers': workers,
        }

    @classmethod
    def _running_tasks_count(cls, event_worker, inspected_worker):
        active = inspected_worker.get('active')
        if isinstance(active, list):
            return len(active)
        if event_worker is not None:
            return getattr(event_worker, 'active', 0) or 0
        return 0
