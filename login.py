# /// script
# dependencies = ["requests"]
# ///
import json
import requests

email, password = open("login.txt").read().split("\n")[:2]

r = requests.post(
    "https://qualitrain.netpulse.com/np/exerciser/login",
    data={"username": email.strip(), "password": password.strip(), "relogin": "false"},
    headers={
        "User-Agent": "QualitrainContainer/6.6.1 (com.qualitrain.fitness; build:3080; iOS 26.5.2) Alamofire/5.9.1",
        "X-NP-API-Version": "1.5",
        "X-NP-APP-Version": "6.6.1",
        "Accept": "application/json",
    },
)
print(r.status_code)
r.raise_for_status()
me = r.json()
json.dump({"JSESSIONID": r.cookies.get("JSESSIONID"), "me": me}, open("session.json", "w"), indent=2)
print({k: me.get(k) for k in ["uuid", "homeClubUuid", "homeClubName", "chainUuid", "membershipType"]})
