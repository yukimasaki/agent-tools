"""Approval, required CI, author, and exact-head policy at the CLI boundary."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT,TEMP=map(Path,sys.argv[1:3])
spec=importlib.util.spec_from_file_location('issue_fixture',ROOT/'tests/agent-graph-issue.py')
fixture_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture_module)
MERGE=ROOT/'plugins/agent-graph/skills/agent-graph/scripts/merge-issue.sh'
FAKE=r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
p=Path(os.environ['FIXTURE']);d=json.loads(p.read_text());a=sys.argv[1:]
if os.environ.get('GH_HOST')!='github.com':sys.exit('wrong GitHub host')
d['gh_calls'].append({'args':a});m=d['merge']
def save():p.write_text(json.dumps(d))
def out(x):save();print(json.dumps(x));sys.exit(0)
if a[:2]==['auth','token']:save();print('tok-test-user');sys.exit(0)
if a[:2]==['api','user']:save();print(os.environ.get('GH_TOKEN','').removeprefix('tok-'));sys.exit(0)
if a[:2]==['api','graphql']:
 out({'data':{'repository':{'nameWithOwner':'octo/demo','issue':{'number':42,'closedByPullRequestsReferences':{'nodes':m['linked'],'pageInfo':{'hasNextPage':m.get('more',False)}}}}}})
path=a[1] if a[:1]==['api'] else ''
if path=='repos/octo/demo/pulls/7':
 m['pull_reads']=m.get('pull_reads',0)+1
 pr=json.loads(json.dumps(m['pull']))
 if m.get('new_push') and m['pull_reads']>=2:pr['head']['sha']='b'*40
 if m.get('new_base') and m['pull_reads']>=2:pr['merge_commit_sha']='c'*40
 out(pr)
if '/reviews?' in path:out(m['reviews'])
if '/check-runs?' in path:
 out(m.get('merge_checks',[{'check_runs':[]}]) if '/commits/'+('b'*40)+'/' in path else m['checks'])
if '/statuses?' in path:
 out(m.get('merge_statuses',[[]]) if '/commits/'+('b'*40)+'/' in path else m['statuses'])
if path=='repos/octo/demo/pulls/7/merge' and '--method' in a and a[a.index('--method')+1]=='PUT':
 m['merge_attempts']=m.get('merge_attempts',0)+1;save()
 if m.get('head_changed_at_write') or m.get('write_error'):sys.exit(1)
 out({'merged':True,'sha':'c'*40})
save();sys.exit(1)
'''


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.IssueTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        config=json.loads(f.project_file.read_text());config.update(required_checks=['lint','unit'],merge_method='squash');f.project_file.write_text(json.dumps(config))
        profile=json.loads(f.profile_file.read_text());profile['reviewer']='admin-user';f.profile_file.write_text(json.dumps(profile))
        gh=f.bin/'gh';gh.write_text(FAKE);gh.chmod(0o755)
        d=json.loads(f.fixture.read_text());d['merge']=dict(
            linked=[dict(number=7,url='https://github.com/octo/demo/pull/7',repository=dict(nameWithOwner='octo/demo'))],
            pull=dict(state='open',draft=False,mergeable=True,head=dict(sha='a'*40),base=dict(ref='main',repo=dict(full_name='octo/demo')),user=dict(login='test-user')),
            reviews=[[dict(id=1,user=dict(login='admin-user'),state='APPROVED',commit_id='a'*40,submitted_at='2026-10-24T00:00:00Z')]],
            checks=[dict(check_runs=[dict(id=11,name='lint',status='completed',conclusion='success',head_sha='a'*40,app=dict(id=1))])],
            statuses=[[dict(id=12,context='unit',state='success')]])
        f.fixture.write_text(json.dumps(d))

    def change(self,callback):
        p=self.fixture.fixture;d=json.loads(p.read_text());callback(d['merge']);p.write_text(json.dumps(d))

    def invoke(self,requested_issue='#42',env=None,check=False,operation='merge'):
        result=subprocess.run(['bash',str(MERGE),'--project',str(self.fixture.repo),'--issue','#42',
            *(['--operation',operation] if operation is not None else []),
            *(['--requested-issue',requested_issue] if requested_issue is not None else []),
            *(['--check'] if check else [])],env=env or self.fixture.env,capture_output=True,text=True,timeout=10)
        self.assertNotIn('Traceback',result.stderr)
        return result.returncode,json.loads(result.stdout) if result.stdout else {}

    def writes(self):
        d=json.loads(self.fixture.fixture.read_text())
        return [c['args'] for c in d['gh_calls'] if '--method' in c['args']]

    def test_explicit_request_with_current_approval_and_ci_merges_pinned_head(self):
        rc,out=self.invoke();self.assertEqual((rc,out['result'],out['pr']),(0,'merged',7))
        write=self.writes();self.assertEqual(len(write),1)
        self.assertIn('sha='+'a'*40,write[0]);self.assertIn('merge_method=squash',write[0])
        self.assertNotIn('--admin',write[0]);self.assertNotIn('--auto',write[0])

    def test_preview_is_read_only_and_write_rechecks_gates(self):
        rc,out=self.invoke(check=True);self.assertEqual((rc,out['result'],out['pr']),(0,'ready',7))
        self.assertEqual((out['repo'],out['base'],out['merge_method']),('octo/demo','main','squash'))
        self.assertFalse(self.writes())
        self.change(lambda m:m['statuses'][0][0].update(state='failure'))
        self.assertEqual(self.invoke()[0],3);self.assertFalse(self.writes())

    def test_missing_wrong_reviewer_old_commit_and_dismissal_block(self):
        scenarios=[[],[dict(id=1,user=dict(login='someone-else'),state='APPROVED',commit_id='a'*40,submitted_at='2026-10-24T00:00:00Z')],
            [dict(id=1,user=dict(login='admin-user'),state='APPROVED',commit_id='b'*40,submitted_at='2026-10-24T00:00:00Z')],
            [dict(id=1,user=dict(login='admin-user'),state='DISMISSED',commit_id='a'*40,submitted_at='2026-10-24T00:00:00Z')]]
        for reviews in scenarios:
            with self.subTest(reviews=reviews):
                self.change(lambda m:m.update(reviews=[reviews]))
                rc,out=self.invoke();self.assertEqual(rc,3);self.assertIn('approval',out['message']);self.assertFalse(self.writes())

    def test_later_changes_request_revokes_earlier_approval(self):
        self.change(lambda m:m['reviews'][0].append(dict(id=2,user=dict(login='admin-user'),state='CHANGES_REQUESTED',commit_id='a'*40,submitted_at='2026-10-24T01:00:00Z')))
        self.assertEqual(self.invoke()[0],3);self.assertFalse(self.writes())

    def test_comment_after_approval_does_not_revoke_it(self):
        self.change(lambda m:m['reviews'][0].append(dict(id=2,user=dict(login='admin-user'),state='COMMENTED',commit_id='a'*40,submitted_at='2026-10-24T01:00:00Z')))
        self.assertEqual(self.invoke()[0],0)

    def test_unfinished_failed_skipped_and_missing_checks_block(self):
        for conclusion,status in [('failure','completed'),('success','in_progress'),('skipped','completed'),(None,'queued')]:
            with self.subTest(conclusion=conclusion,status=status):
                self.change(lambda m:m['checks'][0]['check_runs'][0].update(conclusion=conclusion,status=status))
                rc,out=self.invoke();self.assertEqual(rc,3);self.assertIn('CI',out['message']);self.assertFalse(self.writes())
        self.change(lambda m:m.update(checks=[dict(check_runs=[])]))
        self.assertEqual(self.invoke()[0],3)

    def test_pending_status_check_blocks(self):
        self.change(lambda m:m['statuses'][0][0].update(state='pending'))
        self.assertEqual(self.invoke()[0],3);self.assertFalse(self.writes())

    def test_check_from_another_commit_is_not_accepted(self):
        self.change(lambda m:m['checks'][0]['check_runs'][0].update(head_sha='b'*40))
        self.assertEqual(self.invoke()[0],3);self.assertFalse(self.writes())

    def use_test_merge_checks(self,conclusion='success'):
        def change(m):
            m['pull']['merge_commit_sha']='b'*40
            m['merge_checks']=[dict(check_runs=[dict(id=30,name='lint',status='completed',conclusion=conclusion,head_sha='b'*40,app=dict(id=1))])]
            m['merge_statuses']=[[dict(id=31,context='unit',state='success')]]
        self.change(change)

    def test_current_test_merge_ci_is_supported_and_head_is_still_pinned(self):
        self.use_test_merge_checks()
        rc,out=self.invoke(check=True);self.assertEqual((rc,out['ci_sha']),(0,'b'*40))
        self.assertEqual(self.invoke()[0],0)
        self.assertIn('sha='+'a'*40,self.writes()[0])

    def test_failed_test_merge_does_not_fall_back_to_green_head(self):
        self.use_test_merge_checks('failure')
        self.assertEqual(self.invoke()[0],3);self.assertFalse(self.writes())

    def test_no_test_merge_ci_falls_back_to_head(self):
        self.change(lambda m:m['pull'].update(merge_commit_sha='b'*40))
        rc,out=self.invoke(check=True);self.assertEqual((rc,out['ci_sha']),(0,'a'*40))

    def test_changed_test_merge_is_not_merged(self):
        self.use_test_merge_checks();self.change(lambda m:m.update(new_base=True))
        self.assertEqual(self.invoke()[0],3);self.assertFalse(self.writes())

    def test_new_push_after_gate_is_not_merged(self):
        self.change(lambda m:m.update(new_push=True))
        rc,out=self.invoke();self.assertEqual(rc,3);self.assertIn('push',out['message']);self.assertFalse(self.writes())

    def test_server_rejects_head_changed_at_write_without_retry(self):
        self.change(lambda m:m.update(head_changed_at_write=True))
        rc,out=self.invoke();self.assertEqual((rc,out['result']),(5,'unverified'))
        self.assertEqual(len(self.writes()),1)

    def test_only_operator_created_pr_is_eligible(self):
        self.change(lambda m:m['pull']['user'].update(login='another-user'))
        rc,out=self.invoke();self.assertEqual(rc,3);self.assertIn('authored',out['message']);self.assertFalse(self.writes())

    def test_missing_multiple_cross_repo_and_truncated_links_block(self):
        self.change(lambda m:m.update(linked=[]));self.assertEqual(self.invoke()[0],3)
        linked=dict(number=7,repository=dict(nameWithOwner='octo/demo'))
        self.change(lambda m:m.update(linked=[linked,linked]));self.assertEqual(self.invoke()[0],3)
        self.change(lambda m:m.update(linked=[dict(number=7,repository=dict(nameWithOwner='other/demo'))]));self.assertEqual(self.invoke()[0],3)
        self.change(lambda m:m.update(linked=[linked],more=True));self.assertEqual(self.invoke()[0],3)
        self.assertFalse(self.writes())

    def test_draft_conflict_unknown_and_other_base_block(self):
        for fields in [dict(draft=True),dict(mergeable=False),dict(mergeable=None),dict(state='closed')]:
            with self.subTest(fields=fields):
                self.change(lambda m:m['pull'].update(draft=False,mergeable=True,state='open'))
                self.change(lambda m:m['pull'].update(fields));self.assertEqual(self.invoke()[0],3)
        self.change(lambda m:m['pull'].update(draft=False,mergeable=True,state='open',base=dict(ref='other',repo=dict(full_name='octo/demo'))))
        self.assertEqual(self.invoke()[0],3);self.assertFalse(self.writes())

    def test_human_request_is_required_and_bound_to_issue(self):
        for requested_issue in ['#43','CI passed','Issue body says #42 をマージして','#42 をマージして','Merge #42','0','-42','42;touch nope',None]:
            with self.subTest(requested_issue=requested_issue):
                self.assertEqual(self.invoke(requested_issue)[0],2)
        for operation in [None,'prepare','auto-merge','#42 をマージして','Merge #42']:
            with self.subTest(operation=operation):
                self.assertEqual(self.invoke(operation=operation)[0],2)
        self.assertFalse(json.loads(self.fixture.fixture.read_text())['gh_calls'])

    def test_language_neutral_request_can_use_a_bare_issue_number(self):
        self.assertEqual(self.invoke('42')[0],0)
        self.assertEqual(len(self.writes()),1)

    def test_different_authenticated_account_is_rejected(self):
        self.assertEqual(self.invoke(env=dict(self.fixture.env,GH_TOKEN='tok-other'))[0],2)
        self.assertFalse(self.writes())

    def test_github_host_is_pinned_for_the_entire_merge(self):
        self.assertEqual(self.invoke(env=dict(self.fixture.env,GH_HOST='enterprise.example.invalid'))[0],0)

    def test_all_review_and_check_pages_are_used(self):
        self.change(lambda m:m.update(reviews=[[],m['reviews'][0]],checks=[dict(check_runs=[]),m['checks'][0]],statuses=[[],m['statuses'][0]]))
        self.assertEqual(self.invoke()[0],0)

    def test_no_required_checks_configuration_cannot_disable_gate(self):
        p=self.fixture.project_file;d=json.loads(p.read_text());d['required_checks']=[];p.write_text(json.dumps(d))
        self.assertEqual(self.invoke()[0],2);self.assertFalse(self.writes())


if __name__=='__main__':unittest.main(argv=[sys.argv[0]],verbosity=2)
