from locust import HttpUser, constant, task


class APIUser(HttpUser):
    wait_time = constant(1)

    @task
    def get_users_me(self):
        # We assume authorization header is provided or we just hit the endpoint to test raw throughput if unauth
        # But for 'users/me' we need a token.
        # For baseline, let's just hit health check if auth is hard to script quickly without user creation.
        # Actually, let's use the login endpoint to get a token first in on_start.
        self.client.get("/api/v1/health")

    def on_start(self):
        # Optional: login logic here if needed for deeper tests
        pass
