import os
from unittest.mock import Mock, patch

from tests.unit import AsyncHTTPTestCase


class ScaleEcsWorkerServiceTests(AsyncHTTPTestCase):
    def test_scale_requires_config(self):
        with patch.dict(os.environ, {'FLOWER_UNAUTHENTICATED_API': 'true'}):
            res = self.post('/api/aws/ecs/scale-worker-service', body='')
        self.assertEqual(400, res.code)
        self.assertIn(b'Configure AWS settings first', res.body)

    def test_scale_increments_desired_count(self):
        self._app.aws_config = {
            'region': 'us-east-1',
            'ecs_cluster': 'flower-cluster',
            'ecs_service': 'celery-worker',
        }
        ecs = Mock()
        ecs.describe_services.return_value = {
            'services': [{'desiredCount': 2}],
            'failures': [],
        }
        boto3 = Mock()
        boto3.client.return_value = ecs

        with patch.dict(os.environ, {'FLOWER_UNAUTHENTICATED_API': 'true'}), \
                patch('flower.api.aws.boto3', boto3):
            res = self.post('/api/aws/ecs/scale-worker-service', body='')

        self.assertEqual(200, res.code)
        self.assertIn(b'"previous_desired_count": 2', res.body)
        self.assertIn(b'"new_desired_count": 3', res.body)
        boto3.client.assert_called_once_with('ecs', region_name='us-east-1')
        ecs.describe_services.assert_called_once_with(
            cluster='flower-cluster',
            services=['celery-worker'],
        )
        ecs.update_service.assert_called_once_with(
            cluster='flower-cluster',
            service='celery-worker',
            desiredCount=3,
        )
