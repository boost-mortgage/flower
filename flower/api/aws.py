import logging
import time

from tornado import web

from . import BaseApiHandler

try:
    import boto3
except ImportError:  # pragma: no cover - exercised through runtime error path
    boto3 = None

logger = logging.getLogger(__name__)


SSO_DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code'
AWS_SSO_START_URL = 'https://ssoins-6684f969b2b45bcf.portal.us-east-2.app.aws'
AWS_REGION = 'us-east-2'
AWS_SSO_REGION = 'us-east-2'
AWS_SSO_ROLE_NAME = 'AdministratorAccess'


def _require_boto3():
    if boto3 is None:
        raise web.HTTPError(500, 'boto3 is required for AWS integration')


def _sso_config(config):
    start_url = config.get('sso_start_url') or AWS_SSO_START_URL
    sso_region = config.get('sso_region') or AWS_SSO_REGION
    account_id = config.get('sso_account_id')
    role_name = config.get('sso_role_name') or AWS_SSO_ROLE_NAME
    if not (start_url and sso_region and account_id and role_name):
        raise web.HTTPError(400, 'Configure AWS SSO settings first')
    return start_url, sso_region, account_id, role_name


def _ecs_client(config):
    region = AWS_REGION
    if config.get('auth_method') != 'sso':
        raise web.HTTPError(400, 'AWS SSO authentication is required')

    _, sso_region, account_id, role_name = _sso_config(config)
    access_token = config.get('sso_access_token')
    expires_at = config.get('sso_access_token_expires_at') or 0
    if not access_token or expires_at <= int(time.time()):
        raise web.HTTPError(401, 'AWS SSO login is required or has expired')

    sso = boto3.client('sso', region_name=sso_region)
    role = sso.get_role_credentials(
        accountId=account_id,
        roleName=role_name,
        accessToken=access_token,
    )
    credentials = role['roleCredentials']
    return boto3.client(
        'ecs',
        region_name=region,
        aws_access_key_id=credentials['accessKeyId'],
        aws_secret_access_key=credentials['secretAccessKey'],
        aws_session_token=credentials['sessionToken'],
    )


class StartAwsSsoLogin(BaseApiHandler):
    @web.authenticated
    def post(self):
        _require_boto3()
        config = dict(getattr(self.application, 'aws_config', {}) or {})
        config.update({
            'auth_method': 'sso',
            'region': AWS_REGION,
            'ecs_cluster': self.get_argument(
                'ecs_cluster', config.get('ecs_cluster', '')),
            'ecs_service': self.get_argument(
                'ecs_service', config.get('ecs_service', '')),
            'sso_start_url': AWS_SSO_START_URL,
            'sso_region': AWS_SSO_REGION,
            'sso_account_id': self.get_argument(
                'sso_account_id', config.get('sso_account_id', '')),
            'sso_role_name': self.get_argument(
                'sso_role_name', config.get('sso_role_name', AWS_SSO_ROLE_NAME)
            ).strip() or AWS_SSO_ROLE_NAME,
        })
        self.application.aws_config = config
        start_url, sso_region, _, _ = _sso_config(config)

        try:
            oidc = boto3.client('sso-oidc', region_name=sso_region)
            client = oidc.register_client(
                clientName='flower',
                clientType='public',
            )
            authorization = oidc.start_device_authorization(
                clientId=client['clientId'],
                clientSecret=client['clientSecret'],
                startUrl=start_url,
            )
        except Exception as exc:
            logger.exception('Failed to start AWS SSO login')
            raise web.HTTPError(500, f'Failed to start AWS SSO login: {exc}') from exc

        self.application.aws_sso_device = {
            'client_id': client['clientId'],
            'client_secret': client['clientSecret'],
            'device_code': authorization['deviceCode'],
            'expires_at': int(time.time()) + authorization.get('expiresIn', 600),
            'interval': authorization.get('interval', 5),
            'sso_region': sso_region,
        }
        self.write({
            'message': 'AWS SSO login started',
            'verification_uri': authorization.get('verificationUri'),
            'verification_uri_complete': authorization.get('verificationUriComplete'),
            'user_code': authorization.get('userCode'),
            'interval': authorization.get('interval', 5),
            'expires_in': authorization.get('expiresIn'),
        })


class CompleteAwsSsoLogin(BaseApiHandler):
    @web.authenticated
    def post(self):
        _require_boto3()
        device = getattr(self.application, 'aws_sso_device', {}) or {}
        if not device:
            raise web.HTTPError(400, 'Start AWS SSO login first')
        if device.get('expires_at', 0) <= int(time.time()):
            raise web.HTTPError(400, 'AWS SSO login expired; start again')

        try:
            oidc = boto3.client('sso-oidc', region_name=device['sso_region'])
            token = oidc.create_token(
                clientId=device['client_id'],
                clientSecret=device['client_secret'],
                grantType=SSO_DEVICE_GRANT,
                deviceCode=device['device_code'],
            )
        except Exception as exc:
            error = getattr(exc, 'response', {}).get('Error', {})
            code = error.get('Code')
            if code == 'AuthorizationPendingException':
                self.set_status(202)
                self.write({
                    'message': 'Waiting for AWS SSO authorization',
                    'pending': True,
                    'interval': device.get('interval', 5),
                })
                return
            if code == 'SlowDownException':
                self.set_status(202)
                self.write({
                    'message': 'AWS SSO requested slower polling',
                    'pending': True,
                    'interval': device.get('interval', 5) + 5,
                })
                return
            logger.exception('Failed to complete AWS SSO login')
            raise web.HTTPError(500, f'Failed to complete AWS SSO login: {exc}') from exc

        config = getattr(self.application, 'aws_config', {}) or {}
        config['auth_method'] = 'sso'
        config['sso_access_token'] = token['accessToken']
        config['sso_access_token_expires_at'] = int(time.time()) + token.get('expiresIn', 0)
        self.application.aws_config = config
        self.application.aws_sso_device = {}
        self.write({'message': 'AWS SSO login complete', 'logged_in': True})


def _ecs_name(arn):
    return arn.rsplit('/', 1)[-1]


def _worker_service_config(application):
    config = getattr(application, 'aws_config', {}) or {}
    config['region'] = AWS_REGION
    cluster = config.get('ecs_cluster')
    service = config.get('ecs_service')
    if not (cluster and service):
        raise web.HTTPError(400, 'Configure AWS settings first')
    return config, cluster, service


def _describe_worker_service(ecs, cluster, service):
    described = ecs.describe_services(cluster=cluster, services=[service])
    services = described.get('services') or []
    failures = described.get('failures') or []
    if not services:
        reason = failures[0].get('reason', 'not found') if failures else 'not found'
        raise web.HTTPError(404, f"ECS service '{service}' was not found: {reason}")
    return services[0]


class ListEcsClusters(BaseApiHandler):
    @web.authenticated
    def get(self):
        config = getattr(self.application, 'aws_config', {}) or {}
        config['region'] = AWS_REGION

        _require_boto3()
        try:
            ecs = _ecs_client(config)
            clusters = []
            paginator = ecs.get_paginator('list_clusters')
            for page in paginator.paginate():
                clusters.extend(_ecs_name(arn) for arn in page.get('clusterArns', []))
        except web.HTTPError:
            raise
        except Exception as exc:
            logger.exception('Failed to list ECS clusters')
            raise web.HTTPError(500, f'Failed to list ECS clusters: {exc}') from exc

        self.write({'clusters': sorted(clusters)})


class ListEcsServices(BaseApiHandler):
    @web.authenticated
    def get(self):
        config = getattr(self.application, 'aws_config', {}) or {}
        cluster = self.get_argument('cluster', config.get('ecs_cluster', ''))
        if not cluster:
            raise web.HTTPError(400, 'Choose an ECS cluster first')

        _require_boto3()
        try:
            ecs = _ecs_client(config)
            services = []
            paginator = ecs.get_paginator('list_services')
            for page in paginator.paginate(cluster=cluster):
                services.extend(_ecs_name(arn) for arn in page.get('serviceArns', []))
        except web.HTTPError:
            raise
        except Exception as exc:
            logger.exception('Failed to list ECS services for %s', cluster)
            raise web.HTTPError(500, f'Failed to list ECS services: {exc}') from exc

        self.write({'services': sorted(services)})


class GetEcsWorkerService(BaseApiHandler):
    @web.authenticated
    def get(self):
        _require_boto3()

        try:
            config, cluster, service = _worker_service_config(self.application)
            ecs = _ecs_client(config)
            worker_service = _describe_worker_service(ecs, cluster, service)
        except web.HTTPError:
            raise
        except Exception as exc:
            logger.exception('Failed to describe ECS service %s/%s', cluster, service)
            raise web.HTTPError(500, f'Failed to describe ECS service: {exc}') from exc

        self.write({
            'cluster': cluster,
            'service': service,
            'desired_count': worker_service.get('desiredCount', 0),
            'running_count': worker_service.get('runningCount', 0),
            'pending_count': worker_service.get('pendingCount', 0),
        })


class ScaleEcsWorkerService(BaseApiHandler):
    @web.authenticated
    def post(self):
        _require_boto3()

        try:
            config, cluster, service = _worker_service_config(self.application)
            ecs = _ecs_client(config)
            worker_service = _describe_worker_service(ecs, cluster, service)

            direction = self.get_argument('direction', 'up')
            if direction not in ('up', 'down'):
                raise web.HTTPError(400, 'Scale direction must be up or down')

            current_desired_count = worker_service.get('desiredCount', 0)
            if direction == 'down' and current_desired_count <= 1:
                raise web.HTTPError(400, 'ECS service must keep at least 1 worker')

            delta = 1 if direction == 'up' else -1
            new_desired_count = current_desired_count + delta

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
                f"Scaled {direction} ECS service '{service}' from "
                f'{current_desired_count} to {new_desired_count}'
            ),
            'cluster': cluster,
            'service': service,
            'previous_desired_count': current_desired_count,
            'new_desired_count': new_desired_count,
        })
