"""Exercise the public startup command with a stateful fake herdr."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT, TEMP = map(Path, sys.argv[1:3])
START = ROOT / "plugins/agent-graph/skills/agent-graph/scripts/start.sh"
FAKE = r'''#!/usr/bin/env python3
import hashlib,json,os,sys,time
from pathlib import Path
p=Path(os.environ['FIXTURE']); d=json.loads(p.read_text()); a=sys.argv[1:]
d['calls'].append(a)
def save(): p.write_text(json.dumps(d))
def emit(**result): save(); print(json.dumps({'result':result}))
def fail(code): save(); print(json.dumps({'error':{'code':code}}),file=sys.stderr);sys.exit(1)
mode=d['mode']
if a[:2]==['agent','list']:
 if mode=='api_hang':time.sleep(10)
 agents=([d['agent']] if d.get('agent') else [])+d.get('others',[])
 emit(agents=agents)
elif a[:2]==['tab','create']:
 if mode=='tab_lost': fail('timeout')
 if mode=='invalid_pane_id':emit(tab={'tab_id':'w1:t7'},root_pane={'pane_id':3});sys.exit(0)
 emit(tab={'tab_id':'w1:t7'},root_pane={'pane_id':'w1:p7'})
elif a[:2]==['pane','get']:
 pane={'pane_id':'w1:p7','tab_id':'w1:t7','workspace_id':'w1','cwd':d['cwd']}
 if mode=='wrong_workspace':pane['workspace_id']='w2'
 if mode=='wrong_cwd':pane['cwd']=str(Path(d['cwd']).parent)
 emit(pane=pane)
elif a[:2]==['agent','start']:
 if mode=='no_agent':fail('timeout')
 kind=a[a.index('--kind')+1]
 d['agent']={'pane_id':'w1:p7','tab_id':'w1:t7','workspace_id':'w1','cwd':d['cwd'],
 'agent':kind,'name':a[2],'agent_status':'idle','interactive_ready':True,'terminal_id':'terminal-fixture',
 'agent_session':{'kind':'id','value':'session-one'}}
 if mode=='wrong_kind':d['agent']['agent']='pi'
 if mode=='wrong_name':d['agent']['name']='someone-else'
 if mode in ('timeout_unnamed','trust'):d['agent']['name']=None
 if mode=='trust':d['agent']['agent_status']='blocked'
 if mode=='unknown':d['agent']['agent_status']='unknown'
 if mode=='session_before_prompt':d['agent']['agent_status']='working'
 if mode in ('no_session','late_session'):d['agent'].pop('agent_session')
 if mode=='null_cwd':d['agent']['cwd']=None
 if mode=='invalid_session':d['agent']['agent_session']['value']=123
 if mode=='timeout_unnamed':fail('timeout')
 emit(agent=d['agent'])
elif a[:2]==['pane','process-info']:
 emit(process_info={'foreground_process_group_id':int(os.environ.get('GROUP_PID','999999999')),'shell_pid':1})
elif a[:2]==['agent','read']:
 if mode in ('ack_after_idle_render', 'opencode_receipt_busy', 'opencode_receipt_conversation') and d.get('prompted'):
  d['receipt_reads']=d.get('receipt_reads',0)+1
  if d['receipt_reads']>=2:
   Path(d['receipt']).write_text(json.dumps({'run_id':'test-run','brief_sha256':hashlib.sha256(Path(d['brief']).read_bytes()).hexdigest()}))
 if mode=='session_before_prompt' and d['agent']['agent_status']=='working':
  d['agent']['agent_session']['value']='session-replaced-before-prompt'
  d['agent']['agent_status']='idle'
 save()
 if mode=='read_fail':sys.exit(1)
 if mode=='trust':print('Do you trust this folder?\n❯ 1. Yes')
 elif mode=='unrecognized':print('An unfamiliar screen')
 elif mode=='historical_input':print('❯ earlier instructions\nAn unfamiliar current screen')
 elif mode=='historical_blank_input':print('❯\nAn unfamiliar current screen')
 elif mode=='placeholder':print('❯\u00a0Try "how does <filepath> work?"\n──────\n  ⏵⏵ accept edits on')
 elif mode=='ack_after_idle_render' and d.get('prompted'):print('❯ Submitted brief\nThinking...')
 elif mode=='wrapped_effort':print('❯ Try "edit index.html to..."\n'+'─'*75+'\n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents\n'+' '*55+'◐ medium · /effort')
 elif mode=='not_logged_in':print('❯\n'+'─'*75+'\n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n'+' '*41+'Not logged in · Run /login')
 elif mode=='late_ready':d['late_reads']=d.get('late_reads',0)+1;save();print('❯ ' if d['late_reads']>3 else 'An unfamiliar screen')
 elif mode.startswith('opencode_'):
  # Observed OpenCode 2.1.4, 111x69 and 56x69. Initial input is centered.
  field='┃  Ask anything… "What is the tech stack of this project?"'
  if mode=='opencode_narrow':field='┃  Ask anything… "What is the tech stack of this'
  if mode=='opencode_empty':field='┃'
  if mode=='opencode_typed':field='┃  draft instructions'
  screen='\n'*34+'┃\n'+field+'\n┃\n┃  Build auto · DeepSeek V4.1 Flash Free OpenCode\n╹'+'▀'*50+'\n/ DeepSeek V4.1 Flash Free | ctx (200K) --%  shift+t'
  if mode=='opencode_wide':screen+='ab agents  ctrl+p commands\n'+'\n'*25+'2.1.4'
  if mode=='opencode_reasoning':screen=screen.replace('DeepSeek V4.1 Flash Free OpenCode', 'GLM-5.3-Flash Free OpenCode · high')
  if mode=='opencode_busy':screen=screen.replace('/ DeepSeek','⬝⬝esc interrupt DeepSeek')
  if mode=='opencode_receipt_busy' and d.get('prompted'):screen=screen.replace('/ DeepSeek','⬝⬝esc interrupt DeepSeek')
  if mode=='opencode_shell':screen+=' ↓ 1 shell'
  if mode=='opencode_historical':screen+='\nPermission required\nAllow once'
  if mode=='opencode_unknown_footer':screen+='\nUnknown current menu'
  if mode=='opencode_past_confirmation' or (mode=='opencode_receipt_conversation' and d.get('prompted')):
   screen='A dependency update is available\nDo you trust this folder?\nEnter to confirm · Esc to cancel\n'+screen
  print(screen)
 else:print('› ' if d['agent']['agent']=='codex' else '❯ ')
elif a[:2]==['agent','rename']:
 d['agent']['name']=a[3];emit(agent=d['agent'])
elif a[:2]==['agent','prompt']:
 d['prompted']=True
 receipt={'run_id':'test-run','brief_sha256':hashlib.sha256(Path(d['brief']).read_bytes()).hexdigest()}
 if mode=='wrong_receipt':receipt['run_id']='old-run'
 if mode not in ('pending','pending_lost','session_change','ack_after_idle_render','opencode_receipt_busy','opencode_receipt_conversation'):
  dest=Path(d['receipt']);tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(receipt));tmp.replace(dest)
 if mode=='session_change':d['agent']['agent_session']['value']='session-two'
 if mode=='late_session':d['agent']['agent_session']={'kind':'id','value':'session-after-first-prompt'}
 if mode in ('prompt_lost','pending_lost'):fail('timeout')
 emit(type='agent_prompted')
elif a[:2]==['notification','show']:emit(type='notification_shown')
else:fail('unexpected_command')
'''


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.cwd = self.path / 'work with spaces'; self.cwd.mkdir()
        self.bin = self.path / 'bin'; self.bin.mkdir()
        (self.bin / 'herdr').write_text(FAKE); (self.bin / 'herdr').chmod(0o755)
        self.brief = self.path / 'brief.md'; self.brief.write_text('Read this task.\n')
        self.receipt = self.path / 'receipt.json'
        self.fixture = self.path / 'fixture.json'
        self.fixture.write_text(json.dumps(dict(mode='normal', calls=[], cwd=str(self.cwd),
            brief=str(self.brief), receipt=str(self.receipt))))
        self.env = dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ['PATH'],
                        FIXTURE=str(self.fixture), HERDR_ENV='1')

    def set(self, **changes):
        data = json.loads(self.fixture.read_text()); data.update(changes)
        self.fixture.write_text(json.dumps(data))

    def calls(self, group):
        return [a for a in json.loads(self.fixture.read_text())['calls'] if a[:2] == group.split()]

    def run_start(self, *extra, native=None, timeout=4000, env=None):
        args = ['bash', str(START), '--workspace', 'w1', '--cwd', str(self.cwd), '--name', 'impl-demo42',
            '--kind', 'claude', '--brief', str(self.brief), '--report', str(self.path/'report.md'),
            '--receipt', str(self.receipt), '--run-id', 'test-run', '--timeout', str(timeout), *extra]
        if native: args += ['--', *native]
        result = subprocess.run(args, env=env or self.env, text=True, capture_output=True, timeout=5)
        return result.returncode, json.loads(result.stdout)

    def test_tab_to_receipt_and_argument_boundaries(self):
        rc, out = self.run_start(native=['--model', 'model with spaces', '--settings', '{"literal":"$(touch injected)"}'])
        self.assertEqual((rc, out['result'], out['pane']), (0, 'started', 'w1:p7'))
        tab = self.calls('tab create')[0]
        self.assertIn('--no-focus', tab); self.assertEqual(tab[tab.index('--workspace')+1], 'w1')
        start = self.calls('agent start')[0]
        self.assertIn('model with spaces', start); self.assertIn('{"literal":"$(touch injected)"}', start)
        self.assertEqual(len(self.calls('agent prompt')), 1)
        prompt = self.calls('agent prompt')[0][3]
        self.assertIn(str(self.brief), prompt); self.assertIn(str(self.receipt), prompt)
        log = Path(str(self.receipt)+'.start.jsonl').read_text()
        self.assertNotIn('model with spaces', log); self.assertNotIn('$(touch', log)

    def test_timeout_repairs_only_unnamed_agent(self):
        self.set(mode='timeout_unnamed')
        rc, _ = self.run_start()
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls('agent rename'), [['agent','rename','w1:p7','impl-demo42']])
        self.assertEqual(len(self.calls('agent start')), 1)

    def test_confirmation_keeps_pane_and_can_resume_without_second_start(self):
        self.set(mode='trust')
        rc, out = self.run_start()
        self.assertEqual((rc, out['result']), (4, 'needs_confirmation'))
        self.assertFalse(self.calls('agent prompt'))
        data = json.loads(self.fixture.read_text()); data['mode']='normal';data['agent']['agent_status']='idle'
        self.fixture.write_text(json.dumps(data))
        rc, _ = self.run_start('--pane', 'w1:p7')
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls('tab create')), 1); self.assertEqual(len(self.calls('agent start')), 1)

    def test_acknowledged_resume_sends_nothing_twice(self):
        self.assertEqual(self.run_start()[0], 0)
        self.assertEqual(self.run_start('--pane', 'w1:p7')[0], 0)
        self.assertEqual(len(self.calls('agent prompt')), 1)
        self.assertEqual(len(self.calls('agent start')), 1)

    def test_lost_prompt_reply_can_still_confirm_receipt(self):
        self.set(mode='prompt_lost')
        self.assertEqual(self.run_start()[0], 0)
        self.assertEqual(len(self.calls('agent prompt')), 1)

    def test_lost_prompt_without_receipt_is_not_resent(self):
        self.set(mode='pending_lost')
        self.assertEqual(self.run_start(timeout=1500)[0], 5)
        self.assertEqual(self.run_start('--pane', 'w1:p7')[0], 5)
        self.assertEqual(len(self.calls('agent prompt')), 1)

    def test_codex_kind_uses_native_arguments_without_executable(self):
        self.assertEqual(self.run_start('--kind', 'codex', native=['-m','fixture-model'])[0], 0)
        start=self.calls('agent start')[0]
        self.assertEqual(start[start.index('--kind')+1], 'codex')
        self.assertEqual(start[start.index('--')+1:], ['-m','fixture-model'])

    def test_opencode_live_input_contract_and_receipt(self):
        for mode in ('opencode_wide', 'opencode_narrow', 'opencode_empty', 'opencode_reasoning'):
            with self.subTest(mode=mode):
                self.set(mode=mode, calls=[], agent=None)
                Path(str(self.receipt)+'.start.jsonl').unlink(missing_ok=True)
                self.receipt.unlink(missing_ok=True)
                self.assertEqual(self.run_start('--kind', 'opencode', native=['--auto'])[0], 0)
                self.assertEqual(len(self.calls('agent prompt')), 1)
                self.assertIn('visible', self.calls('agent read')[0])
                self.assertEqual(self.run_start('--kind', 'opencode', '--pane', 'w1:p7', native=['--auto'])[0], 0)
                self.assertEqual(len(self.calls('agent prompt')), 1)

    def test_opencode_drafts_busy_and_historical_boxes_are_not_ready(self):
        for mode in ('opencode_typed', 'opencode_busy', 'opencode_shell',
                     'opencode_historical', 'opencode_unknown_footer'):
            with self.subTest(mode=mode):
                self.set(mode=mode, calls=[], agent=None)
                Path(str(self.receipt)+'.start.jsonl').unlink(missing_ok=True)
                self.assertEqual(self.run_start('--kind', 'opencode', '--ready-grace', '0')[0], 5)
                self.assertFalse(self.calls('agent prompt'))

    def test_opencode_busy_receipt_wait_and_confirmation(self):
        self.set(mode='opencode_receipt_busy')
        self.assertEqual(self.run_start('--kind', 'opencode')[0], 0)
        self.assertEqual(len(self.calls('agent prompt')), 1)
        Path(str(self.receipt)+'.start.jsonl').unlink()
        self.receipt.unlink()
        self.set(mode='trust', calls=[], agent=None)
        self.assertEqual(self.run_start('--kind', 'opencode')[0], 4)
        self.assertFalse(self.calls('agent prompt'))

    def test_opencode_past_confirmation_text_above_current_input(self):
        self.set(mode='opencode_past_confirmation')
        rc, out = self.run_start('--kind', 'opencode')
        self.assertEqual((rc, out['result']), (0, 'started'))
        self.assertEqual(len(self.calls('agent prompt')), 1)

    def test_opencode_receipt_wait_ignores_conversation_confirmation_text(self):
        self.set(mode='opencode_receipt_conversation')
        rc, out = self.run_start('--kind', 'opencode')
        self.assertEqual((rc, out['result']), (0, 'started'))
        self.assertEqual(len(self.calls('agent prompt')), 1)
        self.assertGreaterEqual(json.loads(self.fixture.read_text())['receipt_reads'], 2)

    def test_live_claude_startup_placeholder_is_an_empty_input(self):
        self.set(mode='placeholder')
        self.assertEqual(self.run_start()[0],0)

    def test_session_id_published_after_first_prompt_uses_pinned_process(self):
        self.set(mode='late_session')
        env=dict(self.env,GROUP_PID=str(os.getpid()))
        self.assertEqual(self.run_start(env=env)[0],0)
        self.assertEqual(self.run_start('--pane','w1:p7',env=env)[0],0)
        self.assertEqual(len(self.calls('agent prompt')),1)
        data=json.loads(self.fixture.read_text());data['agent']['agent_session']['value']='cleared-session'
        self.fixture.write_text(json.dumps(data))
        self.assertEqual(self.run_start('--pane','w1:p7',env=env)[0],3)
        self.assertEqual(len(self.calls('agent prompt')),1)

    def test_receipt_wait_does_not_reclassify_busy_render_as_input_readiness(self):
        self.set(mode='ack_after_idle_render')
        self.assertEqual(self.run_start()[0],0)
        self.assertEqual(len(self.calls('agent prompt')),1)

    def test_wrapped_effort_footer_is_an_empty_input(self):
        self.set(mode='wrapped_effort')
        self.assertEqual(self.run_start()[0], 0)
        self.assertEqual(len(self.calls('agent prompt')), 1)

    def test_not_logged_in_footer_is_an_empty_input(self):
        self.set(mode='not_logged_in')
        self.assertEqual(self.run_start()[0], 0)
        self.assertEqual(len(self.calls('agent prompt')), 1)

    def test_screen_recognized_within_ready_grace_receives_one_instruction(self):
        self.set(mode='late_ready')
        self.assertEqual(self.run_start()[0], 0)
        self.assertEqual(len(self.calls('agent prompt')), 1)

    def test_confirmation_stops_without_waiting_for_ready_grace(self):
        self.set(mode='trust')
        began = time.monotonic()
        rc, out = self.run_start('--ready-grace', '10')
        self.assertEqual((rc, out['result']), (4, 'needs_confirmation'))
        self.assertLess(time.monotonic() - began, 3)
        self.assertFalse(self.calls('agent prompt'))

    def test_api_hang_respects_overall_deadline(self):
        self.set(mode='api_hang')
        began=time.monotonic()
        self.assertEqual(self.run_start(timeout=200)[0], 5)
        self.assertLess(time.monotonic()-began,1.2)
        self.assertFalse(self.calls('tab create'))

    def test_unconfirmed_prompt_is_never_repeated(self):
        self.set(mode='pending')
        self.assertEqual(self.run_start(timeout=1500)[0], 5)
        self.assertEqual(self.run_start('--pane', 'w1:p7')[0], 5)
        self.assertEqual(len(self.calls('agent prompt')), 1)
        self.assertEqual(len(self.calls('tab create')), 1)

    def test_old_receipt_is_not_success(self):
        self.set(mode='wrong_receipt')
        self.assertEqual(self.run_start(timeout=1500)[0], 5)

    def test_preexisting_receipt_without_log_is_not_reused(self):
        self.receipt.write_text(json.dumps(dict(run_id='test-run',brief_sha256=hashlib.sha256(self.brief.read_bytes()).hexdigest())))
        self.assertEqual(self.run_start()[0],3)
        self.assertFalse(self.calls('tab create'))

    def test_api_null_cwd_and_invalid_ids_return_structured_errors(self):
        for mode in ('null_cwd','invalid_pane_id'):
            with self.subTest(mode=mode):
                self.set(mode=mode,calls=[],agent=None)
                Path(str(self.receipt)+'.start.jsonl').unlink(missing_ok=True)
                rc,out=self.run_start()
                self.assertEqual((rc,out['result']),(5,'unverified'))
                self.assertFalse(self.calls('agent prompt'))

    def test_incomplete_placement_log_stops_before_mutation(self):
        self.assertEqual(self.run_start()[0],0)
        log=Path(str(self.receipt)+'.start.jsonl')
        rows=[json.loads(line) for line in log.read_text().splitlines()]
        next(row for row in rows if row['event']=='placement').pop('pane')
        log.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.assertEqual(self.run_start()[0],3)
        self.assertEqual(len(self.calls('agent prompt')),1)

    def test_session_replacement_does_not_inherit_receipt(self):
        self.assertEqual(self.run_start()[0], 0)
        data = json.loads(self.fixture.read_text());data['agent']['agent_session']['value']='replacement'
        self.fixture.write_text(json.dumps(data))
        self.assertEqual(self.run_start('--pane', 'w1:p7')[0], 3)
        self.assertEqual(len(self.calls('agent prompt')), 1)

    def test_session_change_while_waiting_is_not_success(self):
        self.set(mode='session_change')
        self.assertEqual(self.run_start()[0], 3)

    def test_session_replacement_before_prompt_receives_no_input(self):
        self.set(mode='session_before_prompt')
        self.assertEqual(self.run_start()[0], 3)
        self.assertFalse(self.calls('agent prompt'))

    def test_wrong_targets_receive_no_inputs(self):
        for mode in ('wrong_kind', 'wrong_name', 'wrong_workspace', 'wrong_cwd'):
            with self.subTest(mode=mode):
                self.set(mode=mode, calls=[], agent=None)
                Path(str(self.receipt)+'.start.jsonl').unlink(missing_ok=True)
                self.assertEqual(self.run_start()[0], 3)
                self.assertFalse(self.calls('agent prompt')); self.assertFalse(self.calls('agent rename'))

    def test_unknown_and_unreadable_screens_stop(self):
        for mode in ('unknown', 'unrecognized', 'historical_input', 'historical_blank_input', 'read_fail', 'no_session', 'invalid_session'):
            with self.subTest(mode=mode):
                self.set(mode=mode, calls=[], agent=None)
                Path(str(self.receipt)+'.start.jsonl').unlink(missing_ok=True)
                self.assertEqual(self.run_start('--ready-grace', '0.5')[0], 5)
                self.assertFalse(self.calls('agent prompt'))

    def test_lost_tab_result_does_not_create_second_tab(self):
        self.set(mode='tab_lost')
        self.assertEqual(self.run_start()[0], 5); self.assertEqual(self.run_start()[0], 5)
        self.assertEqual(len(self.calls('tab create')), 1)
        self.assertFalse(self.calls('agent start'))

    def test_failed_start_is_not_repeated(self):
        self.set(mode='no_agent')
        self.assertEqual(self.run_start()[0], 5); self.assertEqual(self.run_start()[0], 5)
        self.assertEqual(len(self.calls('agent start')), 1)

    def test_changed_brief_or_native_arguments_conflict(self):
        self.assertEqual(self.run_start()[0], 0)
        self.assertEqual(self.run_start(native=['--model', 'different'])[0], 3)
        self.brief.write_text('Changed')
        self.assertEqual(self.run_start()[0], 3)
        self.assertEqual(len(self.calls('agent prompt')), 1)

    def test_unrecorded_pane_cannot_be_adopted(self):
        self.assertEqual(self.run_start('--pane', 'w1:p7')[0], 3)
        self.assertFalse(self.calls('agent start')); self.assertFalse(self.calls('agent rename'))

    def test_name_collision_stops_before_creating_tab(self):
        self.set(others=[dict(name='impl-demo42', pane_id='w2:p1')])
        self.assertEqual(self.run_start()[0], 3)
        self.assertFalse(self.calls('tab create'))

    def test_invalid_arguments_have_no_mutations(self):
        for extra, native in [(('--name','Wrong.Name'), None), ((), ['claude']), ((), ['--prompt','task']), (('--ready-grace','-1'), None)]:
            with self.subTest(extra=extra, native=native):
                self.assertEqual(self.run_start(*extra, native=native)[0], 2)
        self.assertFalse(self.calls('tab create'))
        self.assertEqual(self.run_start(env=dict(self.env, HERDR_ENV='0'))[0], 2)

    def test_corrupt_log_and_concurrent_launch_do_not_retry(self):
        log = Path(str(self.receipt)+'.start.jsonl'); log.write_text('{partial')
        self.assertEqual(self.run_start()[0], 3)
        log.unlink()
        with Path(str(log)+'.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_start()[0], 3)
        self.assertFalse(self.calls('tab create'))

    def test_total_deadline_is_bounded(self):
        self.set(mode='pending')
        began=time.monotonic(); rc,_=self.run_start(timeout=350)
        self.assertEqual(rc,5); self.assertLess(time.monotonic()-began, 1.5)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
