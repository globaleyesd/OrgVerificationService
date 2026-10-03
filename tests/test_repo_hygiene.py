"""Guards for the rules that must never break: nothing secret in git/images/AWS, nothing private in the public repo."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_NAME = re.compile("d" + "oh|d" + "oves", re.I)     # built in pieces so this file doesn't match itself
SKIP_DIRS = {"creds", "data", "__pycache__", ".git", ".pytest_cache"}
SKIP_FILES = {"config.local.yaml", "params.local.json"}     # git-ignored private files


def repo_files():
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and p.name not in SKIP_FILES:
            yield p


def lines(name):
    return {l.strip() for l in (ROOT / name).read_text().splitlines() if l.strip() and not l.startswith("#")}


class IgnoreFileTests(unittest.TestCase):
    REQUIRED = {"creds/", "secrets.local.yaml", "*.local.yaml", ".env", "data/", "*.pem", "*.key", ".aws/"}

    def test_gitignore_blocks_credentials(self):
        self.assertTrue(self.REQUIRED <= lines(".gitignore"), self.REQUIRED - lines(".gitignore"))

    def test_dockerignore_blocks_credentials(self):
        self.assertTrue(self.REQUIRED <= lines(".dockerignore"), self.REQUIRED - lines(".dockerignore"))

    def test_gitignore_also_blocks_private_deploy_params(self):
        self.assertIn("deploy/params.local.json", lines(".gitignore"))

    def test_dockerfile_never_copies_secrets(self):
        for l in (ROOT / "Dockerfile").read_text().splitlines():
            if l.strip().upper().startswith(("COPY", "ADD")):
                self.assertNotRegex(l.lower(), r"creds|secret|\.local|\.env|\.pem|\.key", l)

    def test_credential_folder_name_is_configurable(self):
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
        self.assertIn("path", cfg["credentials"])
        self.assertEqual(cfg["credentials"]["backend"], "file")


class PublicRepoTests(unittest.TestCase):
    def test_no_private_name_anywhere_in_committable_files(self):
        hits = [str(p.relative_to(ROOT)) for p in repo_files()
                if p.suffix not in {".png", ".zip", ".pyc"} and PRIVATE_NAME.search(p.read_text(errors="ignore"))]
        self.assertEqual(hits, [])

    def test_no_default_switch_password_in_docs_or_config(self):
        needle = "56" + "78"
        for p in list(ROOT.glob("docs/*.md")) + [ROOT / "README.md", ROOT / "config.yaml", ROOT / "deploy" / "README.md"]:
            if p.exists():
                self.assertNotIn(needle, p.read_text(), str(p))

    def test_no_key_like_strings(self):
        pat = re.compile(r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-ant-[A-Za-z0-9_-]{20,}")
        for p in repo_files():
            if p.suffix not in {".png", ".zip", ".pyc"} and p.name != "test_deploy.py":
                self.assertIsNone(pat.search(p.read_text(errors="ignore")), str(p))


class TemplateSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class L(yaml.SafeLoader):
            pass

        def multi(loader, suffix, node):
            if isinstance(node, yaml.ScalarNode):
                return {"!" + suffix: loader.construct_scalar(node)}
            if isinstance(node, yaml.SequenceNode):
                return {"!" + suffix: loader.construct_sequence(node, deep=True)}
            return {"!" + suffix: loader.construct_mapping(node, deep=True)}

        L.add_multi_constructor("!", multi)
        cls.text = (ROOT / "deploy/cloudformation/stack.yaml").read_text()
        cls.t = yaml.load(cls.text, Loader=L)
        cls.res = cls.t["Resources"]

    def test_every_bucket_is_private_encrypted_and_tls_only(self):
        buckets = {n: r for n, r in self.res.items() if r["Type"] == "AWS::S3::Bucket"}
        self.assertEqual(set(buckets), {"UiBucket", "DataBucket", "CredsBucket"})
        for n, r in buckets.items():
            pab = r["Properties"]["PublicAccessBlockConfiguration"]
            self.assertTrue(all(pab[k] is True for k in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")), n)
            self.assertIn("BucketEncryption", r["Properties"], n)
            policy = self.res[n + "Policy"]["Properties"]["PolicyDocument"]["Statement"]
            self.assertTrue(any(s.get("Sid") == "DenyInsecureTransport" and s["Effect"] == "Deny" for s in policy), n)

    def test_ui_bucket_readable_only_by_this_distribution(self):
        stmts = self.res["UiBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"]
        allow = [s for s in stmts if s["Effect"] == "Allow"]
        self.assertEqual(len(allow), 1)
        self.assertEqual(allow[0]["Principal"], {"Service": "cloudfront.amazonaws.com"})
        self.assertIn("AWS:SourceArn", allow[0]["Condition"]["StringEquals"])

    def test_no_ssh_and_server_reachable_only_from_cloudfront(self):
        rules = self.res["ServerSecurityGroup"]["Properties"]["SecurityGroupIngress"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["FromPort"], 80)
        self.assertIn("SourcePrefixListId", rules[0])
        self.assertNotIn("CidrIp", rules[0])

    def test_viewers_forced_to_https(self):
        cfg = self.res["Distribution"]["Properties"]["DistributionConfig"]
        self.assertEqual(cfg["DefaultCacheBehavior"]["ViewerProtocolPolicy"], "redirect-to-https")
        for b in cfg["CacheBehaviors"]:
            self.assertEqual(b["ViewerProtocolPolicy"], "https-only")

    def test_api_responses_are_never_cached(self):
        b = self.res["Distribution"]["Properties"]["DistributionConfig"]["CacheBehaviors"][0]
        self.assertEqual(b["PathPattern"], "/api/*")
        self.assertEqual(b["CachePolicyId"], "4135ea2d-6df8-44a3-9df3-4b5a84be39ad")   # Managed-CachingDisabled

    def test_security_headers_present(self):
        c = self.res["SecurityHeaders"]["Properties"]["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"]
        for k in ("StrictTransportSecurity", "ContentTypeOptions", "FrameOptions", "ReferrerPolicy", "ContentSecurityPolicy"):
            self.assertIn(k, c)

    def test_server_hardening(self):
        p = self.res["Server"]["Properties"]
        self.assertEqual(p["MetadataOptions"]["HttpTokens"], "required")
        self.assertTrue(p["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
        self.assertEqual(p["CreditSpecification"]["CPUCredits"], "standard")

    def test_no_secret_parameters_and_no_wildcard_allow_principals(self):
        self.assertFalse([k for k, v in self.t["Parameters"].items() if v.get("NoEcho")])
        for name in ("UiBucketPolicy", "DataBucketPolicy", "CredsBucketPolicy"):
            for st in self.res[name]["Properties"]["PolicyDocument"]["Statement"]:
                if st.get("Principal") == "*":
                    self.assertEqual(st["Effect"], "Deny", name)   # only Deny statements may name everyone

    def test_cost_choices_hold(self):
        types = {r["Type"] for r in self.res.values()}
        for expensive in ("AWS::EC2::NatGateway", "AWS::ElasticLoadBalancingV2::LoadBalancer", "AWS::RDS::DBInstance",
                          "AWS::WAFv2::WebACL", "AWS::OpenSearchService::Domain", "AWS::SecretsManager::Secret"):
            self.assertNotIn(expensive, types)
        self.assertEqual(self.res["Distribution"]["Properties"]["DistributionConfig"]["PriceClass"], "PriceClass_100")


class CredentialsBucketTests(TemplateSecurityTests):
    def test_only_the_server_role_can_touch_the_credentials_bucket(self):
        stmts = self.res["CredsBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"]
        deny = [x for x in stmts if x.get("Sid") == "OnlyTheServerRoleMayTouchRecords"][0]
        self.assertEqual(deny["Effect"], "Deny")
        self.assertEqual(deny["Principal"], "*")
        self.assertEqual(deny["Condition"]["StringNotEquals"]["aws:PrincipalArn"], [{"!GetAtt": "ServerRole.Arn"}, {"!GetAtt": "ControlRole.Arn"}])
        for action in ("s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetObjectVersion"):
            self.assertIn(action, deny["Action"])

    def test_credentials_bucket_is_kept_and_versions_expire_quickly(self):
        r = self.res["CredsBucket"]
        self.assertEqual((r["DeletionPolicy"], r["UpdateReplacePolicy"]), ("Retain", "Retain"))
        rule = r["Properties"]["LifecycleConfiguration"]["Rules"][0]
        self.assertLessEqual(rule["NoncurrentVersionExpiration"]["NoncurrentDays"], 7)

    def test_server_role_can_read_and_write_but_not_delete_credentials(self):
        stmts = self.res["ServerRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        by_sid = {x["Sid"]: x for x in stmts if isinstance(x, dict) and "Sid" in x}
        self.assertEqual(sorted(by_sid["CredentialsReadWrite"]["Action"]), ["s3:GetObject", "s3:PutObject"])
        self.assertEqual(by_sid["CredentialsList"]["Action"], "s3:ListBucket")

    def test_no_kms_key_added_for_cost(self):
        self.assertNotIn("AWS::KMS::Key", {r["Type"] for r in self.res.values()})

    def test_server_is_told_the_bucket_name_through_its_environment(self):
        self.assertIn("CREDS_BUCKET=", self.text)
        compose = yaml.safe_load((ROOT / "docker-compose.aws.yml").read_text())
        env = compose["services"]["api"]["environment"]
        self.assertEqual(env["APP_CREDENTIALS_BACKEND"], "s3")
        self.assertIn("CREDS_BUCKET", env["APP_CREDENTIALS_S3_BUCKET"])
        self.assertNotIn("creds", compose["volumes"])           # nothing credential-like on the server disk any more

    def test_deploy_script_still_cannot_upload_to_or_from_credentials(self):
        from deploy import deploy as d
        for bad in ("creds/users/eileen.json", "users/eileen.json", "service_switch.json", "session_key.json"):
            with self.assertRaises(d.DeployError):
                d.assert_safe_upload(bad)
        self.assertEqual(set(d.UPLOAD_ALLOWLIST), {"docker-compose.aws.yml", "config.yaml", "config.local.yaml"})

    def test_requirements_include_boto3_for_the_s3_store(self):
        self.assertIn("boto3", (ROOT / "requirements.txt").read_text())


class ControlFunctionTemplateTests(TemplateSecurityTests):
    def test_inline_code_is_identical_to_the_tested_source(self):
        inline = self.res["ControlFunction"]["Properties"]["Code"]["ZipFile"]
        self.assertEqual(inline.strip(), (ROOT / "deploy" / "lambda" / "control.py").read_text().strip())
        self.assertLess(len(inline.encode()), 4096)

    def test_function_is_reachable_only_through_signed_cloudfront_requests(self):
        self.assertEqual(self.res["ControlUrl"]["Properties"]["AuthType"], "AWS_IAM")
        oac = self.res["ControlOriginAccessControl"]["Properties"]["OriginAccessControlConfig"]
        self.assertEqual((oac["OriginAccessControlOriginType"], oac["SigningBehavior"]), ("lambda", "always"))
        for name in ("ControlInvokeUrlPermission", "ControlInvokePermission"):
            p = self.res[name]["Properties"]
            self.assertEqual(p["Principal"], "cloudfront.amazonaws.com")
            self.assertIn("distribution/", p["SourceArn"]["!Sub"])           # only THIS distribution
        self.assertEqual(self.res["ControlInvokeUrlPermission"]["Properties"]["FunctionUrlAuthType"], "AWS_IAM")

    def test_control_path_is_routed_signed_and_never_cached(self):
        cfg = self.res["Distribution"]["Properties"]["DistributionConfig"]
        b = [x for x in cfg["CacheBehaviors"] if x["PathPattern"] == "/control/*"][0]
        self.assertEqual((b["TargetOriginId"], b["ViewerProtocolPolicy"], b["CachePolicyId"]), ("control", "https-only", "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"))
        origin = [o for o in cfg["Origins"] if o.get("Id") == "control"][0]
        self.assertEqual(origin["CustomOriginConfig"]["OriginProtocolPolicy"], "https-only")
        self.assertIn("OriginAccessControlId", origin)
        self.assertEqual(cfg["CacheBehaviors"][0]["PathPattern"], "/api/*")   # order matters for the tests above

    def test_api_goes_to_the_server_when_on_and_to_the_switch_function_at_zero(self):
        cfg = self.res["Distribution"]["Properties"]["DistributionConfig"]
        api = [x for x in cfg["CacheBehaviors"] if x["PathPattern"] == "/api/*"][0]
        self.assertEqual(api["TargetOriginId"], {"!If": ["PowerOn", "api", "control"]})
        conditional = [o["!If"] for o in cfg["Origins"] if "!If" in o]
        self.assertEqual([(c[0], c[1]["Id"], c[2]) for c in conditional], [("PowerOn", "api", {"!Ref": "AWS::NoValue"})])

    def test_control_role_is_least_privilege(self):
        stmts = self.res["ControlRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        by = {x["Sid"]: x for x in stmts if "Sid" in x}
        self.assertEqual(sorted(by["StartStopThisServerOnly"]["Action"]), ["ec2:StartInstances", "ec2:StopInstances"])
        self.assertIn("instance/", by["StartStopThisServerOnly"]["Resource"]["!Sub"])
        self.assertEqual(by["StartStopThisServerOnly"]["Condition"], {"StringEquals": {"aws:ResourceTag/Project": {"!Ref": "ProjectName"}}})
        self.assertEqual(by["WriteLockoutRecordOnly"]["Action"], "s3:PutObject")
        self.assertTrue(by["WriteLockoutRecordOnly"]["Resource"]["!Sub"].endswith("/switch_lockout.json"))
        reads = by["ReadSwitchRecords"]["Resource"]
        self.assertEqual([r["!Sub"].rsplit("/", 1)[1] for r in reads], ["service_switch.json", "switch_lockout.json"])
        wild = [x["Sid"] for x in stmts if x.get("Resource") == "*"]
        self.assertEqual(wild, ["ReadServerState"])                      # describe is the only thing that cannot be narrowed

    def test_stopped_server_gets_the_friendly_offline_answer(self):
        errs = self.res["Distribution"]["Properties"]["DistributionConfig"]["CustomErrorResponses"]
        self.assertEqual({e["ErrorCode"] for e in errs}, {502, 504})
        for e in errs:
            self.assertEqual((e["ResponseCode"], e["ResponsePagePath"], e["ErrorCachingMinTTL"]), (503, "/offline.json", 0))
        self.assertEqual(json.loads((ROOT / "app" / "web" / "offline.json").read_text()), {"detail": "Service offline", "offline": True})

    def test_function_logs_are_kept_only_briefly(self):
        self.assertLessEqual(self.res["ControlLogGroup"]["Properties"]["RetentionInDays"], 14)

    def test_server_runs_without_its_own_switch_on_aws(self):
        compose = yaml.safe_load((ROOT / "docker-compose.aws.yml").read_text())
        self.assertEqual(compose["services"]["api"]["environment"]["APP_SERVICE_SWITCH_MODE"], "aws")

    def test_no_new_costly_services(self):
        types = {r["Type"] for r in self.res.values()}
        for t in ("AWS::ApiGateway::RestApi", "AWS::ApiGatewayV2::Api", "AWS::StepFunctions::StateMachine", "AWS::DynamoDB::Table"):
            self.assertNotIn(t, types)


class PowerLevelTemplateTests(TemplateSecurityTests):
    """The project can be taken to zero (no server, disk or IP) and back by the Control Center's power adapter."""

    def test_power_parameter_defaults_to_on(self):
        p = self.t["Parameters"]["Power"]
        self.assertEqual((p["Default"], p["AllowedValues"]), ("on", ["on", "zero"]))

    def test_server_disk_ip_and_schedules_exist_only_when_powered(self):
        for name in ("Server", "ServerIp", "ServerIpAssociation"):
            self.assertEqual(self.res[name]["Condition"], "PowerOn", name)
        for name in ("StartSchedule", "StopSchedule"):
            self.assertEqual(self.res[name]["Condition"], "ScheduleActive", name)
        self.assertEqual(self.t["Conditions"]["ScheduleActive"], {"!And": [{"!Condition": "ScheduleOn"}, {"!Condition": "PowerOn"}]})
        self.assertTrue(self.res["Server"]["Properties"]["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"])

    def test_documents_and_credentials_survive_zero(self):
        for name in ("DataBucket", "CredsBucket"):
            self.assertNotIn("Condition", self.res[name])
            self.assertEqual(self.res[name]["DeletionPolicy"], "Retain")

    def test_database_is_backed_up_before_zero_and_restored_before_the_app_starts(self):
        ud = self.res["Server"]["Properties"]["UserData"]["Fn::Base64"]["!Sub"]
        self.assertIn("cat > /opt/app/backup.sh", ud)
        self.assertIn("pg_dump -U app -d app --clean --if-exists", ud)
        restore, app_start = ud.index("db-latest.sql.gz - --region"), ud.index("up -d --remove-orphans")
        self.assertLess(restore, app_start)                     # restore first, so the app never creates empty tables first
        self.assertIn('[ -s "$f" ] || { echo "empty backup"; exit 1; }', ud)

    def test_power_function_code_is_the_tested_source_and_has_no_url(self):
        inline = self.res["PowerFunction"]["Properties"]["Code"]["ZipFile"]
        self.assertEqual(inline.strip(), (ROOT / "deploy" / "lambda" / "power.py").read_text().strip())
        self.assertLess(len(inline.encode()), 4096)
        self.assertEqual(self.res["PowerFunction"]["Properties"]["FunctionName"], {"!Sub": "${ProjectName}-power"})
        for r in self.res.values():   # nothing exposes it: no function URL, no resource-based permission
            if r["Type"] in ("AWS::Lambda::Url", "AWS::Lambda::Permission"):
                target = r["Properties"].get("TargetFunctionArn") or r["Properties"].get("FunctionName")
                self.assertNotIn("PowerFunction", json.dumps(target))

    def test_power_role_can_change_only_this_stack_and_this_projects_server(self):
        stmts = self.res["PowerRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        by = {x["Sid"]: x for x in stmts if "Sid" in x}
        self.assertEqual(by["ThisStackOnly"]["Resource"], {"!Ref": "AWS::StackId"})
        self.assertEqual(by["StartStopThisProjectsServer"]["Condition"], {"StringEquals": {"aws:ResourceTag/Project": {"!Ref": "ProjectName"}}})
        self.assertEqual(by["HandTheServerItsRole"]["Resource"], {"!GetAtt": "ServerRole.Arn"})
        self.assertIn("distribution/${Distribution}", by["RepointTheDistribution"]["Resource"]["!Sub"])
        self.assertEqual(by["RememberTheLastAction"]["Resource"], {"!Sub": "${DataBucket.Arn}/control/*"})
        flat = [x["!If"][1] if "!If" in x else x for x in stmts]          # the schedule statement exists only with schedules
        for x in flat:
            actions = x["Action"] if isinstance(x["Action"], list) else [x["Action"]]
            self.assertNotIn("*", actions)
            self.assertEqual([a for a in actions if a.startswith("iam:")], [] if "PassRole" not in str(actions) else ["iam:PassRole"])


class PageRoutingTests(TemplateSecurityTests):
    def test_template_csp_matches_the_app_csp(self):
        from app.pages import CSP
        policy = self.res["SecurityHeaders"]["Properties"]["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"]["ContentSecurityPolicy"]
        self.assertEqual(policy["ContentSecurityPolicy"], CSP)
        self.assertNotIn("unsafe-inline", policy["ContentSecurityPolicy"])

    def test_rewrite_function_is_attached_to_page_requests(self):
        assoc = self.res["Distribution"]["Properties"]["DistributionConfig"]["DefaultCacheBehavior"]["FunctionAssociations"]
        self.assertEqual(assoc[0]["EventType"], "viewer-request")
        # never attached to the API behaviour
        for b in self.res["Distribution"]["Properties"]["DistributionConfig"]["CacheBehaviors"]:
            self.assertNotIn("FunctionAssociations", b)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_rewrite_function_matches_the_app_page_map(self):
        from app.pages import PAGES
        code = self.res["UrlRewrite"]["Properties"]["FunctionCode"]
        script = code + """
        const out = {};
        for (const u of %s) { const r = handler({request: {uri: u}}); out[u] = r.uri || ('REDIRECT ' + r.headers.location.value); }
        console.log(JSON.stringify(out));
        """ % json.dumps(["/", "/ask", "/add", "/review", "/signin", "/ask/", "/add/", "/review/", "/style.css", "/common.js", "/ask.html", "/other"])
        res = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        for route, filename in PAGES.items():
            if route != "/":
                self.assertEqual(res[route], "/" + filename, route)
        self.assertEqual(res["/ask/"], "REDIRECT /ask")
        self.assertEqual(res["/add/"], "REDIRECT /add")
        for untouched in ("/", "/style.css", "/common.js", "/ask.html", "/other"):
            self.assertEqual(res[untouched], untouched)


class ComposeTests(unittest.TestCase):
    def test_aws_compose_passes_no_secrets(self):
        c = yaml.safe_load((ROOT / "docker-compose.aws.yml").read_text())
        self.assertEqual(set(c["secrets"]), {"db_password"})
        text = (ROOT / "docker-compose.aws.yml").read_text().lower()
        for bad in ("api_key", "aws_access", "aws_secret"):
            self.assertNotIn(bad, text)
        self.assertIsNone(re.search(r"postgres_password\s*:", text), "database password must come from a file, not a literal")

    def test_local_compose_publishes_to_localhost_only(self):
        c = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        for port in c["services"]["api"]["ports"]:
            self.assertTrue(port.startswith("127.0.0.1:"), port)


if __name__ == "__main__":
    unittest.main()
