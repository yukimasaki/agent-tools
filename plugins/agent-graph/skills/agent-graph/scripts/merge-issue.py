#!/usr/bin/env python3
"""Merge one Issue's own-account PR only on an explicit human request."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class Stop(Exception):
    def __init__(self, message, code=3):
        self.message, self.code = message, code


def run(argv, env=None):
    try:
        value = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise Stop('Cannot confirm the GitHub result. Ask the administrator to inspect the PR before retrying.', 5)
    if value.returncode:
        raise Stop('The GitHub check or operation did not finish. Ask the administrator to inspect the PR.', 5)
    return value.stdout.strip()


def obj(value):
    if not isinstance(value, dict):
        raise Stop('Invalid GitHub or configuration data. Ask the administrator to inspect it.', 5)
    return value


def login(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*', value):
        raise Stop('Configure the administrator and operator GitHub accounts.', 2)
    return value


def rows(value):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise Stop('Cannot read the GitHub list. Ask the administrator to inspect it.', 5)
    return value


def same(value, expected):
    return isinstance(value, str) and value.lower() == expected.lower()


def load(path):
    try:
        return obj(json.loads(path.read_text()))
    except (OSError, ValueError):
        raise Stop('Cannot read entry configuration. Ask the administrator to configure it.', 2)


class Merger:
    def __init__(self, args):
        self.args = args
        common = Path(run(['git', '-C', args.project, 'rev-parse', '--path-format=absolute', '--git-common-dir']))
        root = common.parent
        config = load(root / '.agent-graph/project.json')
        project, self.repo = config.get('project'), config.get('repo')
        if (config.get('version') != 1 or not isinstance(project, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,11}', project) or
                not isinstance(self.repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.repo)):
            raise Stop('Ask the administrator to check the project configuration.', 2)
        remote = run(['git', '-C', str(root), 'config', '--get', 'remote.origin.url'])
        match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?', remote)
        if not match or match.group(1).lower() != self.repo.lower():
            raise Stop('Configuration and repository do not match. Ask the administrator to check them.', 2)
        self.base = config.get('base')
        self.required = config.get('required_checks')
        self.method = config.get('merge_method', 'squash')
        if (not isinstance(self.base, str) or not self.base or self.method not in {'squash','merge','rebase'} or
                not isinstance(self.required, list) or not self.required or
                any(not isinstance(c, str) or not c for c in self.required) or len(set(self.required)) != len(self.required)):
            raise Stop('Ask the administrator to configure required CI and the merge method.', 2)
        config_root = Path(os.environ.get('AGENT_GRAPH_CONFIG_ROOT') or
                           str(Path(os.environ.get('XDG_CONFIG_HOME') or str(Path.home()/'.config'))/'agent-graph'))
        profile = load(config_root/'projects'/(project+'.json'))
        if profile.get('version') != 1 or not isinstance(profile.get('repo'), str) or profile['repo'].lower() != self.repo.lower():
            raise Stop('The machine profile repository does not match.', 2)
        self.account = login(profile.get('github_user'))
        self.reviewer = login(profile.get('reviewer'))
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        github_env = dict(os.environ,GH_HOST='github.com')
        if not token:
            token = run(['gh','auth','token','--user',self.account],github_env)
        if not token:
            raise Stop('GitHub authentication is required. Ask the administrator to check it.', 2)
        self.env = dict(github_env, GH_TOKEN=token); self.env.pop('GITHUB_TOKEN',None)
        if run(['gh','api','user','--jq','.login'],self.env).lower() != self.account.lower():
            raise Stop('The authenticated GitHub account differs from configuration. Ask the administrator to check it.', 2)

    def gh(self, *args):
        try:
            return json.loads(run(['gh',*args],self.env))
        except ValueError:
            raise Stop('Cannot read the GitHub result. Ask the administrator to inspect the PR.', 5)

    def api(self, path, *, paged=False):
        args=['api',path]
        if paged:args+=['--paginate','--slurp']
        return self.gh(*args)

    def linked_pr(self):
        owner, name = self.repo.split('/')
        query = '''query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){nameWithOwner issue(number:$number){number closedByPullRequestsReferences(first:2){nodes{number url repository{nameWithOwner}} pageInfo{hasNextPage}}}}}'''
        response=obj(self.gh('api','graphql','-f','query='+query,'-f','owner='+owner,'-f','name='+name,'-F','number='+str(self.args.issue)))
        if response.get('errors'):
            raise Stop('Cannot confirm the PR linked to this Issue. Ask the administrator to inspect it.',5)
        repository=obj(obj(response.get('data')).get('repository'))
        if not same(repository.get('nameWithOwner'),self.repo):
            raise Stop('Cannot confirm the target repository.',5)
        issue=obj(repository.get('issue'))
        if issue.get('number') != self.args.issue:
            raise Stop('Cannot confirm the Issue number mapping.',5)
        connection=obj(issue.get('closedByPullRequestsReferences'))
        candidates=rows(connection.get('nodes'))
        if obj(connection.get('pageInfo')).get('hasNextPage') is not False or len(candidates)>1:
            raise Stop('Multiple PRs are linked. Ask the administrator to identify the target.')
        if not candidates:
            raise Stop('No unmerged PR is linked to this Issue. Ask the administrator to check it.')
        pr=candidates[0]
        if not same(obj(pr.get('repository')).get('nameWithOwner'),self.repo) or type(pr.get('number')) is not int or pr['number']<=0:
            raise Stop('A PR in another repository cannot be merged. Ask the administrator to check it.')
        return pr['number']

    def pull(self, number):
        pr=obj(self.api(f'repos/{self.repo}/pulls/{number}'))
        head=obj(pr.get('head')).get('sha')
        if not isinstance(head,str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',head):
            raise Stop('Cannot confirm the current PR head.',5)
        base=obj(pr.get('base'))
        if (pr.get('state')!='open' or pr.get('draft') is not False or base.get('ref')!=self.base or
                not same(obj(base.get('repo')).get('full_name'),self.repo)):
            raise Stop('Ask the administrator to check the PR state and target branch.')
        if not same(obj(pr.get('user')).get('login'),self.account):
            raise Stop('The PR was not authored by the configured operator account. Ask the administrator to check it.')
        if pr.get('mergeable') is not True:
            raise Stop('Cannot confirm mergeability. Ask the administrator to check conflicts and the GitHub mergeability result.')
        test_merge=pr.get('merge_commit_sha')
        if test_merge is not None and (not isinstance(test_merge,str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',test_merge)):
            raise Stop('Cannot confirm the current test-merge commit.',5)
        return head,test_merge

    def approval(self, number, head):
        pages=self.api(f'repos/{self.repo}/pulls/{number}/reviews?per_page=100',paged=True)
        reviews=[]
        for page in pages if isinstance(pages,list) else [None]:reviews.extend(rows(page))
        relevant=[]
        for review in reviews:
            user=obj(review.get('user')).get('login')
            if same(user,self.reviewer) and review.get('state') not in {'APPROVED','CHANGES_REQUESTED','DISMISSED','COMMENTED','PENDING'}:
                raise Stop('Cannot confirm the administrator review state.',5)
            if isinstance(user,str) and user.lower()==self.reviewer.lower() and review.get('state') in {'APPROVED','CHANGES_REQUESTED','DISMISSED'}:
                if not isinstance(review.get('submitted_at'),str) or type(review.get('id')) is not int:
                    raise Stop('Cannot confirm the administrator review order.',5)
                relevant.append(review)
        latest=max(relevant,key=lambda r:(r['submitted_at'],r['id']),default={})
        if latest.get('state')!='APPROVED' or latest.get('commit_id')!=head:
            raise Stop('Administrator approval is missing. Ask the administrator to approve the current head.')

    def check_results(self, commit):
        pages=self.api(f'repos/{self.repo}/commits/{commit}/check-runs?filter=latest&per_page=100',paged=True)
        check_runs=[]
        for page in pages if isinstance(pages,list) else [None]:check_runs.extend(rows(obj(page).get('check_runs')))
        status_pages=self.api(f'repos/{self.repo}/commits/{commit}/statuses?per_page=100',paged=True)
        statuses=[]
        for page in status_pages if isinstance(status_pages,list) else [None]:statuses.extend(rows(page))
        return check_runs,statuses

    def checks(self, head, test_merge):
        commit=test_merge or head
        check_runs,statuses=self.check_results(commit)
        if test_merge and test_merge!=head and not check_runs and not statuses:
            commit=head
            check_runs,statuses=self.check_results(commit)
        latest={}
        for row in check_runs:
            if row.get('head_sha') != commit:
                raise Stop('CI results do not match the current commit. Ask the administrator to inspect them.')
            key=('check',row.get('name'),obj(row.get('app')).get('id'))
            if not isinstance(key[1],str) or type(row.get('id')) is not int:
                raise Stop('Cannot confirm CI results.',5)
            if key not in latest or row['id']>latest[key]['id']:latest[key]=row
        for row in statuses:
            key=('status',row.get('context'),None)
            if not isinstance(key[1],str) or type(row.get('id')) is not int:
                raise Stop('Cannot confirm CI results.',5)
            if key not in latest or row['id']>latest[key]['id']:latest[key]=row
        missing=[]
        for name in self.required:
            matches=[(key,row) for key,row in latest.items() if key[1]==name]
            if not matches or any((row.get('status')!='completed' or row.get('conclusion')!='success') if key[0]=='check'
                                  else row.get('state')!='success' for key,row in matches):missing.append(name)
        if missing:
            raise Stop('Required CI is incomplete or failed. Ask the administrator to inspect: '+', '.join(missing))
        return commit

    def merge(self):
        number=self.linked_pr()
        head,test_merge=self.pull(number)
        self.approval(number,head);ci_sha=self.checks(head,test_merge)
        if self.args.check:
            return dict(result='ready',repo=self.repo,issue=self.args.issue,pr=number,head_sha=head,ci_sha=ci_sha,base=self.base,merge_method=self.method,reviewer=self.reviewer,
                        message=f'Administrator approval and required CI passed for PR #{number}. No merge has occurred.')
        if self.pull(number)!=(head,test_merge):
            raise Stop('A new push or target update occurred after the review check. Wait for administrator approval and CI.')
        # Recheck gates on this exact head immediately before the write. GitHub
        # branch protections remain authoritative for changes after these reads.
        self.approval(number,head);self.checks(head,test_merge)
        result=obj(self.gh('api',f'repos/{self.repo}/pulls/{number}/merge','--method','PUT',
                           '-f','sha='+head,'-f','merge_method='+self.method))
        if result.get('merged') is not True:
            raise Stop('The merge did not complete. Ask the administrator to inspect the PR.',5)
        return dict(result='merged',repo=self.repo,issue=self.args.issue,pr=number,head_sha=head,merge_method=self.method,merge_sha=result.get('sha'),
                    message=f'Merged PR #{number} for Issue #{self.args.issue}.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default='.')
    parser.add_argument('--issue',required=True)
    parser.add_argument('--operation',required=True,choices=['merge'],help='operation explicitly requested by the latest human input')
    parser.add_argument('--requested-issue',required=True,help='Issue number from that human request, interpreted by the calling skill')
    parser.add_argument('--check',action='store_true',help='read-only preview; the merge call still rechecks every gate')
    args=parser.parse_args()
    try:
        if not re.fullmatch(r'#?[1-9][0-9]{0,9}',args.issue):raise Stop('Check the Issue number.',2)
        args.issue=int(args.issue.lstrip('#'))
        if not re.fullmatch(r'#?[1-9][0-9]{0,9}',args.requested_issue) or int(args.requested_issue.lstrip('#')) != args.issue:
            raise Stop('The latest explicit human merge request must refer to the same Issue.',2)
        output=Merger(args).merge();code=0
    except Stop as error:
        output=dict(result='unverified' if error.code==5 else 'blocked',message=error.message);code=error.code
    except (OSError,ValueError,TypeError,KeyError):
        output=dict(result='unverified',message='Cannot confirm the result. Ask the administrator to inspect the PR before retrying.');code=5
    print(json.dumps(output,ensure_ascii=False));return code


if __name__=='__main__':sys.exit(main())
