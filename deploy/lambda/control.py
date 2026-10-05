"""AWS-only status function behind CloudFront. It reports the server's state to the page and answers /api/* while the
project is at zero (no server). It can NOT start or stop anything: the only way to turn the project on or off is the
Control Center, after its password. Inline in the template (keep under 4096 bytes)."""
import json, os
import boto3
P = os.environ["PROJECT"]
ec2 = boto3.client("ec2")

def server_state():
    f = [{"Name": "tag:Project", "Values": [P]}, {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
    for r in ec2.describe_instances(Filters=f)["Reservations"]:
        for i in r["Instances"]:
            return i["State"]["Name"]
    return "zero"

def out(code, body):
    return {"statusCode": code, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body)}

def handler(event, context):
    raw_path, method = event.get("rawPath", ""), event["requestContext"]["http"]["method"]
    if raw_path.startswith("/api/"):   # routed here only while the project is at zero
        return out(503, {"detail": "Service offline", "offline": True})
    if method != "GET" or raw_path.rstrip("/").rsplit("/", 1)[-1] != "status":
        return out(404, {"detail": "Not found. Turn the project on or off from the Control Center."})
    try:
        st = server_state()
        return out(200, {"state": st, "on": st in ("pending", "running")})
    except Exception as e:
        print("status error", type(e).__name__)
        return out(503, {"detail": "Status is unavailable right now"})
