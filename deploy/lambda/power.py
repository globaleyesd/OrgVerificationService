"""Control Center power adapter (status|on|off|zero). Only the Control Center may invoke it (IAM)."""
import json, os, time
import boto3
P, S, D, K = os.environ["PROJECT"], os.environ["STACK"], os.environ["DATA_BUCKET"], "control/power.json"
ec2, cfn, ssm, s3 = (boto3.client(n) for n in ("ec2", "cloudformation", "ssm", "s3"))
LV = {"running": "running", "pending": "starting", "stopping": "stopping", "stopped": "stopped"}

def server():
    f = [{"Name": "tag:Project", "Values": [P]}, {"Name": "instance-state-name", "Values": list(LV)}]
    for r in ec2.describe_instances(Filters=f)["Reservations"]:
        for i in r["Instances"]:
            return i["InstanceId"], i["State"]["Name"]
    return None, None

def stack():
    s = cfn.describe_stacks(StackName=S)["Stacks"][0]
    return s["StackStatus"], {p["ParameterKey"]: p.get("ParameterValue") for p in s.get("Parameters", [])}

def note(**d):
    s3.put_object(Bucket=D, Key=K, Body=json.dumps(dict(d, at=time.time())).encode(), ServerSideEncryption="AES256")

def status():
    st, ps = stack()
    try: n = json.loads(s3.get_object(Bucket=D, Key=K)["Body"].read())
    except Exception: n = {}
    z, age = ps.get("Power") == "zero", time.time() - n.get("at", 0)
    if st.endswith("_IN_PROGRESS"):
        return {"level": "changing", "detail": "Going %s (%s)" % ("to zero" if z else "on", st)}
    if n.get("phase") == "backup" and age < 1500:
        return {"level": "changing", "detail": "Backing up the database"}
    err = n.get("error", "") if age < 86400 else ""
    if z:
        return {"level": "zero", "detail": err or "Data kept in S3"}
    return {"level": LV.get(server()[1], "unknown"), "detail": err}

def power(v):
    pp = [{"ParameterKey": k, "UsePreviousValue": True} for k in stack()[1] if k != "Power"]
    cfn.update_stack(StackName=S, UsePreviousTemplate=True, Capabilities=["CAPABILITY_IAM"],
                     Parameters=pp + [{"ParameterKey": "Power", "ParameterValue": v}])

def wait(ok, secs):
    end = time.time() + secs
    while time.time() < end:
        if ok():
            return True
        time.sleep(10)

def backup(i, s):
    if s == "stopped":
        ec2.start_instances(InstanceIds=[i])
    up = lambda: any(x["PingStatus"] == "Online" for x in ssm.describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": [i]}])["InstanceInformationList"])
    if not wait(up, 420):
        raise RuntimeError("server not online for backup")
    c = ssm.send_command(InstanceIds=[i], DocumentName="AWS-RunShellScript",
                         Parameters={"commands": ["/opt/app/backup.sh"]})["Command"]["CommandId"]
    r = {}
    def done():
        try: r.update(ssm.get_command_invocation(CommandId=c, InstanceId=i))
        except Exception: return False
        return r["Status"] not in ("Pending", "InProgress", "Delayed")
    wait(done, 600)
    if r.get("Status") != "Success":
        raise RuntimeError("backup failed (%s)" % r.get("Status", "timeout"))

def handler(event, context):
    a = (event or {}).get("action")
    try:
        if a == "status":
            return status()
        st, ps = stack()
        z, (i, s) = ps.get("Power") == "zero", server()
        if a not in ("on", "off", "zero"):
            return {"ok": False, "detail": "Unknown action"}
        if st.endswith("_IN_PROGRESS") or (s in ("pending", "stopping") and not z):
            return {"ok": False, "detail": "Busy, try again in a minute"}
        if a == "on" and z:
            note(phase="rebuild")
            power("on")
        elif a == "on" and s == "stopped":
            ec2.start_instances(InstanceIds=[i])
        elif a == "off" and s == "running":
            ec2.stop_instances(InstanceIds=[i])
        elif a == "zero" and not z:
            note(phase="backup")
            backup(i, s)
            note(phase="remove")
            power("zero")
        return dict(status(), ok=True)
    except Exception as e:
        print("power error", e)
        note(phase="failed", error="Last action failed: %s" % e)
        return {"ok": False, "detail": str(e)}
