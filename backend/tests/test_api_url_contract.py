from app.main import app


def test_backend_exposes_versioned_routes_once():
    routes = {
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, 'methods', None) or set())
    }
    expected = {
        ('POST', '/api/v1/auth/register'),
        ('POST', '/api/v1/auth/login'),
        ('POST', '/api/v1/auth/verify-otp'),
        ('POST', '/api/v1/auth/refresh'),
        ('GET', '/api/v1/me'),
        ('PATCH', '/api/v1/me/profile'),
        ('GET', '/api/v1/transactions'),
        ('POST', '/api/v1/transactions/{tid}/payments'),
        ('POST', '/api/v1/evidence/upload-urls'),
        ('GET', '/api/v1/disputes/{did}'),
        ('GET', '/api/v1/notifications'),
    }
    assert expected <= routes
    assert not any(path.startswith('/api/v1/api/v1/') for _, path in routes)
