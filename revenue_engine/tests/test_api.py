"""HTTP-level tests for the FastAPI app, including the background job runner."""

import time
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient

from api.main import create_app

from .conftest import FILE_NAME, make_supplier, write_supplier_file

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def client(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    with TestClient(create_app()) as c:
        yield c


def wait_for(fetch, done, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = fetch()
        if done(body):
            return body
        time.sleep(0.05)
    raise AssertionError(f"timed out; last response: {body}")


def upload_job(client, supplier_id, tmp_path, file_name=FILE_NAME):
    content = write_supplier_file(tmp_path / "src.xlsx").read_bytes()
    return client.post(
        "/api/jobs",
        files={"file": (file_name, content, XLSX)},
        data={"supplier_profile_id": str(supplier_id), "date_received": "2025-07-02"},
    )


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/health/ready").status_code == 200


def test_meta_is_cacheable(client):
    r = client.get("/api/meta")
    assert r.headers["cache-control"] == "public, max-age=3600"
    assert "entity_code_hint" in r.json()["canonical_fields"]


def test_upload_process_review_export(client, seeded, tmp_path):
    r = upload_job(client, seeded["supplier_id"], tmp_path)
    assert r.status_code == 202
    job = r.json()
    assert job["status"] == "uploaded"
    assert r.headers["location"] == f"/api/jobs/{job['id']}"

    job = wait_for(lambda: client.get(f"/api/jobs/{job['id']}").json(),
                   lambda b: b["status"] not in ("uploaded", "processing"))
    assert job["status"] == "needs_review"
    assert (job["matched_count"], job["unmatched_count"], job["total_revenue"]) == (3, 1, 270.0)

    pending = client.get("/api/jobs", params={"has_pending_reviews": "true"}).json()
    assert [j["id"] for j in pending] == [job["id"]]

    items = client.get(f"/api/jobs/{job['id']}/review-items").json()
    assert items[0]["customer_name_sample"] == "Zyxw Qvbn Clinic"
    decision = client.post(f"/api/review-items/{items[0]['id']}/approval",
                           json={"member_id": seeded["members"]["desert"], "alias_scope": "global"}).json()
    assert decision["job"]["status"] == "completed"

    again = client.post(f"/api/review-items/{items[0]['id']}/dismissal")
    assert again.status_code == 409
    assert again.headers["content-type"] == "application/problem+json"

    preview = client.get(f"/api/jobs/{job['id']}/preview").json()
    assert sorted(r["total_revenue"] for r in preview["upload"]) == [30.0, 50.0, 100.0, 120.0]

    export = client.get(f"/api/jobs/{job['id']}/export")
    assert export.status_code == 200
    assert export.headers["content-type"] == XLSX
    assert "Processed.xlsx" in export.headers["content-disposition"]
    assert openpyxl.load_workbook(BytesIO(export.content)).sheetnames == [
        "Upload", "Validation", "Unmatched", "JDE Mapping"]


def test_failed_processing_is_reported_on_the_job(client, conn, tmp_path):
    supplier_id = make_supplier(conn, column_map={"Nope": "fee_amount"})  # required columns missing
    job = upload_job(client, supplier_id, tmp_path).json()
    job = wait_for(lambda: client.get(f"/api/jobs/{job['id']}").json(),
                   lambda b: b["status"] not in ("uploaded", "processing"))
    assert job["status"] == "failed"
    assert job["error_message"]


def test_upload_rejections(client, seeded):
    r = client.post("/api/jobs", files={"file": ("notes.pdf", b"x", "application/pdf")},
                    data={"supplier_profile_id": str(seeded["supplier_id"]), "date_received": "2025-07-02"})
    assert r.status_code == 422 and "Unsupported file type" in r.json()["detail"]

    big = b"x" * (1024 * 1024 + 1)
    r = client.post("/api/jobs", files={"file": ("big.csv", big, "text/csv")},
                    data={"supplier_profile_id": str(seeded["supplier_id"]), "date_received": "2025-07-02"})
    assert r.status_code == 413

    r = client.post("/api/jobs", files={"file": ("a.csv", b"a", "text/csv")},
                    data={"supplier_profile_id": str(seeded["supplier_id"])})
    assert r.status_code == 422
    assert r.json()["errors"][0]["loc"] == ["body", "date_received"]


def test_payment_id_patch_and_confirmation(client, processed_job):
    job_id = processed_job["job_id"]
    assert client.patch(f"/api/jobs/{job_id}", json={"payment_id": "PAY-X"}).json()["payment_id"] == "PAY-X"
    assert client.patch(f"/api/jobs/{job_id}", json={"payment_id": ""}).status_code == 422
    assert client.post(f"/api/jobs/{job_id}/confirmation").status_code == 409


def test_file_name_inference(client, seeded):
    body = client.get("/api/file-name-inference", params={"file_name": FILE_NAME}).json()
    assert body == {"file_name": FILE_NAME, "supplier_profile_id": seeded["supplier_id"],
                    "supplier_name": "Acme Supply", "date_received": "2025-07-02", "deposit_amount": 300.0}


def test_validation_errors_point_at_the_field(client, conn):
    sid = make_supplier(conn, name="Komet", job_type="pivot_allocation")
    r = client.post(f"/api/suppliers/{sid}/extraction-rules",
                    json={"extraction_method": "named_cell", "rule_name": "Total"})
    assert r.status_code == 422
    assert r.json()["errors"][0] == {"loc": ["body", "cell_address"],
                                     "msg": "Cell Address is required for named_cell.", "type": "value_error"}


def test_tier_c_over_http(client, conn, seeded):
    sid = make_supplier(conn, name="Komet", job_type="pivot_allocation")
    rule = client.post(f"/api/suppliers/{sid}/extraction-rules",
                       json={"extraction_method": "named_cell", "rule_name": "Total", "cell_address": "E8"})
    assert rule.status_code == 201
    rule_id = rule.json()["rule"]["id"]
    notices = client.post(f"/api/suppliers/{sid}/allocation-rules/validation",
                          json={"extraction_rule_name": "Total", "allocation_type": "percent",
                                "allocation_value": 120}).json()
    assert notices[0]["level"] == "warning"
    alloc = client.post(f"/api/suppliers/{sid}/allocation-rules", json={
        "extraction_rule_name": "Total", "member_id": seeded["members"]["general"], "allocation_type": "remainder"})
    assert alloc.status_code == 201
    assert client.delete(f"/api/suppliers/{sid}/extraction-rules/{rule_id}").status_code == 409
    assert client.delete(f"/api/suppliers/{sid}/allocation-rules/{alloc.json()['id']}").status_code == 204
    assert client.delete(f"/api/suppliers/{sid}/extraction-rules/{rule_id}").status_code == 204
    # Rules are scoped to their supplier.
    assert client.delete(f"/api/suppliers/{seeded['supplier_id']}/extraction-rules/{rule_id}").status_code == 404


def test_upload_with_manual_totals(client, conn, seeded, tmp_path):
    sid = make_supplier(conn, name="Komet", job_type="pivot_allocation")
    client.post(f"/api/suppliers/{sid}/extraction-rules", json={"extraction_method": "manual_entry", "rule_name": "Rebate"})
    client.post(f"/api/suppliers/{sid}/allocation-rules", json={
        "extraction_rule_name": "Rebate", "member_id": seeded["members"]["general"], "allocation_type": "remainder"})

    def post(manual_totals):
        content = write_supplier_file(tmp_path / "k.xlsx").read_bytes()
        data = {"supplier_profile_id": str(sid), "date_received": "2025-07-02"}
        if manual_totals is not None:
            data["manual_totals"] = manual_totals
        return client.post("/api/jobs", files={"file": ("Komet.xlsx", content, XLSX)}, data=data)

    assert post(None).status_code == 422
    assert post("[1, 2]").status_code == 422
    assert post('{"Rebate": "lots"}').status_code == 422
    r = post('{"Rebate": 250}')
    assert r.status_code == 202
    job = wait_for(lambda: client.get(f"/api/jobs/{r.json()['id']}").json(),
                   lambda b: b["status"] not in ("uploaded", "processing"))
    assert (job["status"], job["total_revenue"], job["manual_totals"]) == ("completed", 250.0, {"Rebate": 250.0})


def test_member_import_runs_as_task(client, tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Accounts"
    ws.append(["Account ID", "Account Name", "Premier Entity Code", "Billing City", "Billing State/Province",
               "Billing Zip/Postal Code"])
    ws.append(["SF-1", "Lakeside Hospital", "EC100", "Austin", "TX", "78701"])
    buf = BytesIO()
    wb.save(buf)
    r = client.post("/api/member-imports", files={"file": ("Account List.xlsx", buf.getvalue(), XLSX)})
    assert r.status_code == 202
    task = wait_for(lambda: client.get(r.headers["location"]).json(), lambda b: b["status"] in ("succeeded", "failed"))
    assert task["member_import"] == {"inserted": 1, "skipped": 0, "deactivated": 0}
    assert client.get("/api/members", params={"search": "lakeside"}).json()["total"] == 1


def test_ledger_import_failure_is_reported(client):
    wb = openpyxl.Workbook()
    buf = BytesIO()
    wb.save(buf)
    r = client.post("/api/ledger-imports", files={"file": ("Ledger.xlsx", buf.getvalue(), XLSX)},
                    data={"sheet_name": "Missing"})
    task = wait_for(lambda: client.get(r.headers["location"]).json(), lambda b: b["status"] in ("succeeded", "failed"))
    assert task["status"] == "failed" and "Could not read the ledger" in task["error"]
    assert client.get("/api/tasks/nope").status_code == 404


def test_supplier_crud_and_conflict(client):
    body = {"name": "Viscot", "header_row": 1}
    created = client.post("/api/suppliers", json=body)
    assert created.status_code == 201
    assert client.post("/api/suppliers", json=body).status_code == 409
    assert client.post("/api/suppliers", json={**body, "bogus": 1}).status_code == 422
    sid = created.json()["id"]
    assert client.put(f"/api/suppliers/{sid}", json={**body, "fee_rate": 0.5}).json()["fee_rate"] == 0.5


def test_unknown_route_is_a_problem(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["title"] == "Not Found"


def test_openapi_responses_are_fully_typed(client):
    """No response schema may be an untyped object (bare dict / Any)."""
    schema = client.get("/api/openapi.json").json()

    def check(node, where):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" not in node and "$ref" not in node:
                assert "additionalProperties" in node and node["additionalProperties"] is not True, where
            if "properties" in node:
                for name, prop in node["properties"].items():
                    assert prop, f"{where}.{name} has an empty (Any) schema"
            for k, v in node.items():
                check(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                check(v, f"{where}[{i}]")

    check(schema["components"]["schemas"], "components")
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            for status, resp in op["responses"].items():
                if status.startswith(("4", "5")):
                    assert list(resp["content"]) == ["application/problem+json"], (method, path, status)


# --- Serving the built frontend ---------------------------------------------

@pytest.fixture
def spa_client(conn, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside dist")
    monkeypatch.setenv("STATIC_DIR", str(dist))
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def test_deep_links_fall_back_to_index_html(spa_client):
    for path in ("/", "/admin/tier-c?supplier=3&extractionRule=5", "/review"):
        r = spa_client.get(path)
        assert r.status_code == 200
        assert "id=root" in r.text
        assert r.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_cached_and_public_files_served(spa_client):
    r = spa_client.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]
    assert spa_client.get("/favicon.svg").text == "<svg/>"
    assert spa_client.get("/assets/missing.js").status_code == 404


def test_unknown_api_paths_stay_problem_json(spa_client):
    r = spa_client.get("/api/no-such-thing")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")


def test_spa_fallback_never_serves_files_outside_dist(spa_client):
    r = spa_client.get("/..%2Fsecret.txt")
    assert "outside dist" not in r.text


def test_legacy_health_path_and_security_headers(spa_client):
    r = spa_client.get("/_stcore/health")
    assert r.status_code == 200 and r.text == "ok"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_api_only_without_a_built_frontend(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/review").status_code == 404


def test_responses_are_gzipped_when_the_client_accepts_it(spa_client):
    (spa_client.app.state.settings.static_dir / "assets" / "big.js").write_text("x" * 5000)
    r = spa_client.get("/assets/big.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"
