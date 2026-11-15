from django.test import TestCase, override_settings

ORIGIN = "http://localhost:5173"
API = "/api/v1/sample/projects"


@override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN], CORS_ALLOW_CREDENTIALS=True)
class CorsAllowedTests(TestCase):
    def test_allowed_origin_gets_cors_headers_with_credentials(self):
        res = self.client.get(API, HTTP_ORIGIN=ORIGIN)
        self.assertEqual(res["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(res["Access-Control-Allow-Credentials"], "true")

    def test_preflight_allows_the_custom_headers_the_front_sends(self):
        res = self.client.options("/api/v1/runs", HTTP_ORIGIN=ORIGIN, HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
                                  HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-csrftoken,idempotency-key,if-match")
        self.assertEqual(res.status_code, 200)
        allowed = res["Access-Control-Allow-Headers"].lower()
        for header in ("content-type", "x-csrftoken", "idempotency-key", "if-match"):
            self.assertIn(header, allowed)
        self.assertIn("POST", res["Access-Control-Allow-Methods"])

    def test_response_headers_the_front_needs_are_exposed(self):
        exposed = self.client.get(API, HTTP_ORIGIN=ORIGIN)["Access-Control-Expose-Headers"]
        for header in ("ETag", "Idempotent-Replayed", "Content-Disposition"):
            self.assertIn(header, exposed)

    def test_other_origin_is_not_allowed(self):
        res = self.client.get(API, HTTP_ORIGIN="https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", res)

    def test_only_the_api_is_covered(self):
        res = self.client.get("/admin/login/", HTTP_ORIGIN=ORIGIN)
        self.assertNotIn("Access-Control-Allow-Origin", res)


class CorsDefaultTests(TestCase):
    def test_without_configuration_no_other_origin_is_allowed(self):
        res = self.client.get(API, HTTP_ORIGIN=ORIGIN)
        self.assertNotIn("Access-Control-Allow-Origin", res)
        self.assertNotIn("Access-Control-Allow-Credentials", res)
