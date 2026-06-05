from tests.unit import AsyncHTTPTestCase


class TestAwsView(AsyncHTTPTestCase):
    def test_page_renders(self):
        res = self.get('/aws')
        self.assertEqual(200, res.code)
        self.assertIn(b'AWS', res.body)
        self.assertIn(b'ECS service', res.body)

    def test_save_config(self):
        res = self.post('/aws', body={
            'region': 'us-east-1',
            'ecs_cluster': 'flower-cluster',
            'ecs_service': 'celery-worker',
        })
        self.assertEqual(200, res.code)
        self.assertEqual({
            'region': 'us-east-1',
            'ecs_cluster': 'flower-cluster',
            'ecs_service': 'celery-worker',
        }, self._app.aws_config)
