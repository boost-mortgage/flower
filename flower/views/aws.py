from tornado import web

from ..views import BaseHandler


class AwsView(BaseHandler):
    @web.authenticated
    def get(self):
        self.render("aws.html", aws_config=self._aws_config(), saved=False, errors=[])

    @web.authenticated
    def post(self):
        region = self.get_argument('region', default='').strip()
        ecs_cluster = self.get_argument('ecs_cluster', default='').strip()
        ecs_service = self.get_argument('ecs_service', default='').strip()

        errors = []
        if not region:
            errors.append('AWS region is required')
        if not ecs_cluster:
            errors.append('ECS cluster name is required')
        if not ecs_service:
            errors.append('ECS service name is required')

        if errors:
            self.set_status(400)
            self.render("aws.html", aws_config={
                'region': region,
                'ecs_cluster': ecs_cluster,
                'ecs_service': ecs_service,
            }, errors=errors)
            return

        self.application.aws_config = {
            'region': region,
            'ecs_cluster': ecs_cluster,
            'ecs_service': ecs_service,
        }
        self.render("aws.html", aws_config=self._aws_config(), saved=True, errors=[])

    def _aws_config(self):
        return getattr(self.application, 'aws_config', {}) or {}
