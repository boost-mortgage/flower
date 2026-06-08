from tornado import web

from ..views import BaseHandler

AWS_SSO_START_URL = 'https://ssoins-6684f969b2b45bcf.portal.us-east-2.app.aws'
AWS_REGION = 'us-east-2'
AWS_SSO_REGION = 'us-east-2'
AWS_SSO_ROLE_NAME = 'AdministratorAccess'


class AwsView(BaseHandler):
    @web.authenticated
    def get(self):
        self.render("aws.html", aws_config=self._aws_config(), saved=False, errors=[])

    @web.authenticated
    def post(self):
        region = AWS_REGION
        ecs_cluster = self.get_argument('ecs_cluster', default='').strip()
        ecs_service = self.get_argument('ecs_service', default='').strip()
        auth_method = 'sso'
        sso_start_url = AWS_SSO_START_URL
        sso_region = AWS_SSO_REGION
        sso_account_id = self.get_argument('sso_account_id', default='').strip()
        sso_role_name = self.get_argument(
            'sso_role_name', default=AWS_SSO_ROLE_NAME).strip() or AWS_SSO_ROLE_NAME

        errors = []
        if not ecs_cluster:
            errors.append('ECS cluster name is required')
        if not ecs_service:
            errors.append('ECS service name is required')
        if not sso_account_id:
            errors.append('AWS SSO account ID is required')

        config = {
            'region': region,
            'ecs_cluster': ecs_cluster,
            'ecs_service': ecs_service,
            'auth_method': auth_method,
            'sso_start_url': sso_start_url,
            'sso_region': sso_region,
            'sso_account_id': sso_account_id,
            'sso_role_name': sso_role_name,
        }

        if errors:
            self.set_status(400)
            self.render("aws.html", aws_config=config, errors=errors)
            return

        current_config = self._aws_config()
        for key in ('sso_access_token', 'sso_access_token_expires_at'):
            if key in current_config:
                config[key] = current_config[key]

        self.application.aws_config = config
        self.render("aws.html", aws_config=self._aws_config(), saved=True, errors=[])

    def _aws_config(self):
        config = dict(getattr(self.application, 'aws_config', {}) or {})
        config.setdefault('auth_method', 'sso')
        config.setdefault('region', AWS_REGION)
        config.setdefault('sso_start_url', AWS_SSO_START_URL)
        config.setdefault('sso_region', AWS_SSO_REGION)
        config.setdefault('sso_role_name', AWS_SSO_ROLE_NAME)
        return config
