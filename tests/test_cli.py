import argparse
import datetime as dt
import hashlib
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ios_app_conflict_fixer import cli

APP='org.example.mail'
EXT=APP+'.NotificationExtension'
ERROR=f"Error Domain=MIInstallerErrorDomain Code=81 App with identifier {EXT} is already installed, so we can't install app extension with that same identifier."

class EvidenceTests(unittest.TestCase):
    def test_only_explicit_error_is_extracted(self):
        self.assertEqual(cli.conflicts(ERROR+'\n'+ERROR),[EXT])
        self.assertEqual(cli.conflicts('Code=81 '+EXT),[])
        self.assertEqual(cli.conflicts('App with identifier '+EXT+' is already installed'),[])
    def test_scope_system_and_unproven_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'input.txt';log.write_text(ERROR)
            cli.evidence(log,APP,EXT)
            for app,target in [(APP,'org.other.Ext'),(APP,APP),('com.apple.test','com.apple.test.Ext'),(APP,APP+'.Other'),(APP,'bad;id')]:
                with self.subTest(target=target),self.assertRaises(cli.FixerError):cli.evidence(log,app,target)

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.cert=b'synthetic-certificate-not-a-real-credential'
        self.identity=hashlib.sha1(self.cert).hexdigest()
        self.profile={'ExpirationDate':dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)+dt.timedelta(days=1),
            'Entitlements':{'application-identifier':'EXAMPLE123.*','com.apple.developer.team-identifier':'EXAMPLE123','get-task-allow':True},
            'ApplicationIdentifierPrefix':['EXAMPLE123'],'ProvisionedDevices':['TEST-DEVICE'],'DeveloperCertificates':[self.cert]}
    def check(self):
        with patch.object(cli,'run',return_value=plistlib.dumps(self.profile)):
            return cli.signing_profile('synthetic.mobileprovision',self.identity,'TEST-DEVICE',EXT)
    def test_exact_entitlements(self):
        ent=self.check();self.assertEqual(ent['application-identifier'],'EXAMPLE123.'+EXT)
        self.assertEqual(ent['keychain-access-groups'],['EXAMPLE123.'+EXT])
        self.assertEqual(set(ent),{'application-identifier','keychain-access-groups','get-task-allow','com.apple.developer.team-identifier'})
    def test_invalid_profile_constraints(self):
        for key,value in [('ExpirationDate',dt.datetime(2000,1,1)),('ProvisionedDevices',[]),('DeveloperCertificates',[])]:
            old=self.profile[key];self.profile[key]=value
            with self.subTest(key=key),self.assertRaises(cli.FixerError):self.check()
            self.profile[key]=old
        for key,value in [('application-identifier','EXAMPLE123.org.other.*'),('get-task-allow',False)]:
            old=self.profile['Entitlements'][key];self.profile['Entitlements'][key]=value
            with self.subTest(key=key),self.assertRaises(cli.FixerError):self.check()
            self.profile['Entitlements'][key]=old

class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.work=Path(self.tmp.name)
        self.log=self.work/'evidence.txt';self.log.write_text(ERROR)
        self.args=argparse.Namespace(app_id=APP,conflict_id=EXT,log=self.log,udid='TEST-DEVICE',yes=True,allow_data_loss=True)
        self.state={'tool':cli.MARKER,'app_id':APP,'conflict_id':EXT,'udid':'TEST-DEVICE','phase':'built'}
        self.record={'CFBundleIdentifier':EXT,'CFBundleName':'ConflictCleanup','CFBundleDisplayName':'Conflict Cleanup','Path':'/synthetic/ConflictCleanup.app','Container':'/synthetic/container'}
    def tearDown(self):self.tmp.cleanup()
    def test_existing_target_or_parent_stops_before_build(self):
        for target in [[self.record],[]]:
            with patch.object(cli,'require_device'),patch.object(cli,'apps',side_effect=[target,[{'CFBundleIdentifier':APP}]]),patch.object(cli,'build') as build,self.assertRaises(cli.FixerError):cli.repair(self.args)
            build.assert_not_called()
    def test_noninteractive_data_loss_acknowledgment(self):
        self.args.allow_data_loss=False
        with self.assertRaises(cli.FixerError):cli.confirmed(self.args,self.state)
    def test_cleanup_refuses_unfamiliar_app(self):
        with patch.object(cli,'require_device'),patch.object(cli,'apps',return_value=[{'CFBundleIdentifier':EXT,'Path':'/real.app'}]),patch.object(cli,'run') as run,self.assertRaises(cli.FixerError):cli.remove_cleanup(self.work,self.state)
        run.assert_not_called()
    def test_complete_without_disappearance_is_not_success(self):
        with patch.object(cli,'require_device'),patch.object(cli,'apps',return_value=[self.record]),patch.object(cli,'run',return_value=b'Uninstall: Complete'),self.assertRaises(cli.FixerError):cli.remove_cleanup(self.work,self.state)
        self.assertNotEqual(self.state['phase'],'temporary-app-removed')
    def test_successful_lifecycle(self):
        def command(argv,**kwargs):return b'Install: InstallComplete (100%)\nInstall: Complete' if 'install' in argv else b'Uninstall: Complete'
        with patch.object(cli,'require_device'),patch.object(cli,'apps',side_effect=[[],[],[self.record],[self.record],[]]),patch.object(cli,'build',return_value=(self.work,self.work/'ConflictCleanup.app',self.state)),patch.object(cli,'run',side_effect=command) as run:cli.repair(self.args)
        self.assertEqual([c.args[0][3] for c in run.call_args_list],['install','uninstall'])
        self.assertEqual(json.loads((self.work/'state.json').read_text())['phase'],'temporary-app-removed')
    def test_failed_install_is_not_blindly_uninstalled(self):
        with patch.object(cli,'require_device'),patch.object(cli,'apps',side_effect=[[],[],[]]),patch.object(cli,'build',return_value=(self.work,self.work/'ConflictCleanup.app',self.state)),patch.object(cli,'run',return_value=b'Install: Error') as run,self.assertRaises(cli.FixerError):cli.repair(self.args)
        self.assertEqual(run.call_count,1);self.assertEqual(self.state['phase'],'installing')

if __name__=='__main__':unittest.main()
