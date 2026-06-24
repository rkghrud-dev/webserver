import os

from fastapi.testclient import TestClient

os.environ["CLIENT_TOKEN"] = "test-token"
os.environ["ADMIN_PASSWORD"] = "admin-pass"
os.environ["KEY_MANAGER_ROOT"] = os.path.join(os.path.dirname(__file__), "tmp-key")
os.environ["KEY_ROOT"] = os.environ["KEY_MANAGER_ROOT"]
os.environ["KEY_MANAGER_DB"] = os.path.join(os.environ["KEY_MANAGER_ROOT"], "key_manager.db")

import shutil
shutil.rmtree(os.environ["KEY_MANAGER_ROOT"], ignore_errors=True)

from app import app  # noqa: E402


client = TestClient(app)

assert client.get("/health").json() == {"ok": True}
assert "로그인" in client.get("/").text
assert "회원가입" in client.get("/portal/signup").text
assert client.get("/keys/login").status_code == 200

manage = client.get("/keys/manage", params={"name": "홍길동", "shop_name": "테스트몰"})
assert manage.status_code == 200
assert "서버에 키 저장" in manage.text

saved_files = client.post(
    "/api/key-files",
    json={
        "name": "홍길동",
        "shop_name": "테스트몰",
        "files": [
            {
                "name": "coupang_wing_api.txt",
                "text": "vendor_name=\r\nurl=\r\nip=\r\nvendor_id=VID\r\nvendor_user_id=USER\r\nreturn_center_code=RET\r\noutbound_shipping_place_code=OUT\r\naccess_key=AK\r\nsecret_key=SK\r\nexpires_at=\r\n",
            },
            {
                "name": "홈런/쿠팡/coupang_wing_api.txt",
                "text": "vendor_id=VID\r\naccess_key=AK\r\nsecret_key=SK\r\n",
            },
        ],
    },
)
assert saved_files.status_code == 403

admin_login = client.get("/admin/login")
assert admin_login.status_code == 200

admin_page = client.post("/admin", data={"admin_id": "admin", "admin_password": "admin-pass"})
assert admin_page.status_code == 200
assert "회원 승인 관리" in admin_page.text

client.post(
    "/admin/markets/1/approve",
    data={"admin_id": "admin", "admin_password": "admin-pass"},
)

saved_files = client.post(
    "/api/key-files",
    json={
        "name": "홍길동",
        "shop_name": "테스트몰",
        "files": [
            {
                "name": "coupang_wing_api.txt",
                "text": "vendor_name=\r\nurl=\r\nip=\r\nvendor_id=VID\r\nvendor_user_id=USER\r\nreturn_center_code=RET\r\noutbound_shipping_place_code=OUT\r\naccess_key=AK\r\nsecret_key=SK\r\nexpires_at=\r\n",
            },
            {
                "name": "홈런/쿠팡/coupang_wing_api.txt",
                "text": "vendor_id=VID\r\naccess_key=AK\r\nsecret_key=SK\r\n",
            },
        ],
    },
)
assert saved_files.status_code == 200, saved_files.text
assert saved_files.json()["shop_id"] == "S001"
assert saved_files.json()["saved_count"] == 2

shop_file = os.path.join(os.environ["KEY_MANAGER_ROOT"], "S001", "coupang_wing_api.txt")
assert os.path.exists(shop_file)

shops = client.get("/api/shops").json()["shops"]
assert shops[0]["shop_id"] == "S001"
assert shops[0]["owner_name"] == "홍길동"
assert shops[0]["shop_name"] == "테스트몰"

second = client.get("/keys/manage", params={"name": "홍길동", "shop_name": "두번째마켓"})
assert second.status_code == 200
owner_shops = client.get("/api/shops", params={"name": "홍길동"}).json()["shops"]
assert [s["shop_id"] for s in owner_shops] == ["S001", "S002"]
assert [s["shop_name"] for s in owner_shops] == ["테스트몰", "두번째마켓"]

dashboard = client.get("/portal/dashboard", params={"name": "홍길동"})
assert dashboard.status_code == 200
assert "WebOCR 프로그램" in dashboard.text
assert "CafeShipment 프로그램" in dashboard.text
assert "키 설정 페이지" in dashboard.text

portal_admin = client.post("/portal/login", data={"login_id": "admin", "password": "admin-pass"})
assert portal_admin.status_code == 200
assert "회원 승인 관리" in portal_admin.text

same_shop_other_owner = client.get("/keys/manage", params={"name": "김철수", "shop_name": "테스트몰"})
assert same_shop_other_owner.status_code == 200
all_shops = client.get("/api/shops").json()["shops"]
assert [s["shop_id"] for s in all_shops] == ["S001", "S002", "S003"]

assert client.get("/status").status_code == 401

status = client.get("/status", headers={"X-Client-Token": "test-token"})
assert status.status_code == 200
assert status.json()["accounts"]["home"]["configured"] == {
    "access_key": False,
    "secret_key": False,
    "vendor_id": False,
    "return_center_code": False,
    "outbound_shipping_place_code": False,
}

missing = client.post(
    "/coupang/predict-category",
    headers={"X-Client-Token": "test-token"},
    json={"product_name": "무선 청소기"},
)
assert missing.status_code == 503

bad_path = client.post(
    "/coupang/proxy",
    headers={"X-Client-Token": "test-token"},
    json={"method": "GET", "path": "https://example.com", "query": {}},
)
assert bad_path.status_code == 503

setup = client.post(
    "/admin/setup",
    data={
        "admin_password": "admin-pass",
        "base_url": "https://api-gateway.coupang.com",
        "home_access_key": "AK",
        "home_secret_key": "SK",
        "home_vendor_id": "VID",
        "home_vendor_user_id": "USER",
        "home_return_center_code": "RET",
        "home_outbound_shipping_place_code": "OUT",
    },
    follow_redirects=False,
)
assert setup.status_code == 303

status = client.get("/status", headers={"X-Client-Token": "test-token"}).json()
assert status["accounts"]["home"]["configured"] == {
    "access_key": True,
    "secret_key": True,
    "vendor_id": True,
    "return_center_code": True,
    "outbound_shipping_place_code": True,
}

saved = open(
    os.path.join(os.environ["KEY_ROOT"], "홈런", "쿠팡", "coupang_wing_api.txt"),
    encoding="utf-8",
    newline="",
).read()
assert "vendor_id=VID\r\n" in saved
assert "vendor_user_id=USER\r\n" in saved
assert "return_center_code=RET\r\n" in saved
assert "outbound_shipping_place_code=OUT\r\n" in saved

print("coupang gateway smoke ok")
