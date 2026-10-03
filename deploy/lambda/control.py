"""AWS-only service switch. Password-checks, then starts or stops the server. Also answers /api/* while the project
is at zero (no server). Inline in the template (keep under 4096 bytes). Same password record as app/passwords.py."""
import base64, hashlib, hmac, json, os, time
import boto3
B, P = os.environ["CREDS_BUCKET"], os.environ["PROJECT"]
MAXF, LOCK = int(os.environ.get("MAX_FAILS", 5)), float(os.environ.get("LOCK_MIN", 10)) * 60
s3, ec2 = boto3.client("s3"), boto3.client("ec2")

def get(k):
    try:
        return json.loads(s3.get_object(Bucket=B, Key=k + ".json")["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None

def put(k, d):
    s3.put_object(Bucket=B, Key=k + ".json", Body=json.dumps(d).encode(), ServerSideEncryption="AES256")

def good(pw, r):
    try:
        h = hashlib.scrypt(pw.encode(), salt=base64.b64decode(r["salt"]), n=r["n"], r=r["r"], p=r["p"], dklen=r["dklen"])
        return hmac.compare_digest(h, base64.b64decode(r["hash"]))
    except Exception:
        return False

def server():
    f = [{"Name": "tag:Project", "Values": [P]}, {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
    for r in ec2.describe_instances(Filters=f)["Reservations"]:
        for i in r["Instances"]:
            return i["InstanceId"], i["State"]["Name"]
    return None, "zero"

def out(code, body):
    return {"statusCode": code, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body)}

def handler(event, context):
    raw_path, method = event.get("rawPath", ""), event["requestContext"]["http"]["method"]
    if raw_path.startswith("/api/"):   # routed here only while the project is at zero
        return out(503, {"detail": "Service offline", "offline": True})
    path = raw_path.rstrip("/").rsplit("/", 1)[-1]
    try:
        if method == "GET" and path == "status":
            st = server()[1]
            return out(200, {"state": st, "on": st in ("pending", "running")})
        if method != "POST" or path not in ("on", "off"):
            return out(404, {"detail": "Not found"})
        raw = event.get("body") or "{}"
        pw = json.loads(base64.b64decode(raw) if event.get("isBase64Encoded") else raw).get("password")
        now, lk, rec = time.time(), get("switch_lockout") or {}, get("service_switch")
        if now < lk.get("until", 0):
            return out(429, {"detail": "Too many attempts. Try again in %d minute(s)." % -(-(lk["until"] - now) // 60)})
        if rec is None:
            return out(503, {"detail": "The switch password is not set up yet"})
        if not isinstance(pw, str) or not good(pw, rec):
            n = lk.get("n", 0) + 1
            locked = n >= MAXF
            put("switch_lockout", {"n": 0, "until": now + LOCK} if locked else {"n": n, "until": 0})
            return out(429 if locked else 401, {"detail": "Too many attempts. Try again later." if locked else "Wrong password"})
        if lk.get("n"):
            put("switch_lockout", {"n": 0, "until": 0})
        i, st = server()
        if st == "zero":
            return out(409, {"detail": "This project is at zero. Bring it back from the Control Center."})
        if path == "on":
            if st == "stopping":
                return out(409, {"detail": "The server is still shutting down. Try again in a minute."})
            if st == "stopped":
                ec2.start_instances(InstanceIds=[i])
                st = "pending"
        else:
            if st == "pending":
                return out(409, {"detail": "The server is still starting. Try again in a minute."})
            if st == "running":
                ec2.stop_instances(InstanceIds=[i])
                st = "stopping"
        return out(200, {"state": st, "on": st in ("pending", "running")})
    except Exception as e:
        print("control error", type(e).__name__)
        return out(503, {"detail": "Control is unavailable right now"})
