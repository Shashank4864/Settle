import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from app import config

r = httpx.post(f"{config.PAYPAL_BASE}/v1/oauth2/token",
               auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_SECRET),
               data={"grant_type": "client_credentials"}, timeout=30)
print("status:", r.status_code)
j = r.json()
print("app_id:", j.get("app_id"))
print("client id starts with:", config.PAYPAL_CLIENT_ID[:6], "| length:", len(config.PAYPAL_CLIENT_ID))
print("base url:", config.PAYPAL_BASE)
print("merchant email:", config.MERCHANT_EMAIL)
scopes = j.get("scope", "").split()
print("invoicing permissions:", [s for s in scopes if "invoic" in s])
print("total permissions:", len(scopes))
print("\n".join(sorted(scopes)))