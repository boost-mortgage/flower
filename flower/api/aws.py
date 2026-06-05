import logging

from tornado import web

from . import BaseApiHandler

try:
    import boto3
except ImportError:  # pragma: no cover - exercised through runtime error path
    boto3 = None

logger = logging.getLogger(__name__)


class ScaleEcsWorkerService(BaseApiHandler):
    @web.authenticated
    def post(self):
        config = getattr(self.application, 'aws_config', {}) or {}
        region = config.get('region')
        cluster = config.get('ecs_cluster')
        service = config.get('ecs_service')

        if not (region and cluster and service):
            raise web.HTTPError(400, 'Configure AWS settings first')

        if boto3 is None:
            raise web.HTTPError(500, 'boto3 is required to scale an ECS service')

        try:
            ecs = boto3.client('ecs', region_name=region)
            described = ecs.describe_services(cluster=cluster, services=[service])
            services = described.get('services') or []
            failures = described.get('failures') or []
            if not services:
                reason = failures[0].get('reason', 'not found') if failures else 'not found'
                raise web.HTTPError(404, f"ECS service '{service}' was not found: {reason}")

            current_desired_count = services[0].get('desiredCount', 0)
            new_desired_count = current_desired_count + 1
            ecs.update_service(
                cluster=cluster,
                service=service,
                desiredCount=new_desired_count,
            )
        except web.HTTPError:
            raise
        except Exception as exc:
            logger.exception('Failed to scale ECS service %s/%s', cluster, service)
            raise web.HTTPError(500, f'Failed to scale ECS service: {exc}') from exc

        self.write({
            'message': (
                f"Scaled ECS service '{service}' from "
                f'{current_desired_count} to {new_desired_count}'
            ),
            'cluster': cluster,
            'service': service,
            'previous_desired_count': current_desired_count,
            'new_desired_count': new_desired_count,
        })
