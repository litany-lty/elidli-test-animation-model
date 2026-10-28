#!/usr/bin/env python3
"""Isolated Chromium diagnostics; never infer story or aesthetic quality from pixels."""
import io
import json
from pathlib import Path
import sys
import time
from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright


def delta(a,b):
    a,b=Image.open(io.BytesIO(a)).convert('RGB'),Image.open(io.BytesIO(b)).convert('RGB')
    if a.size!=b.size: return 255
    return sum(ImageStat.Stat(ImageChops.difference(a,b)).mean)/3


def verify(directory):
    started=time.monotonic()
    checks=[]; errors=[]; blocked=[]; measurements={}
    def check(name,weight,status,reason):
        checks.append(dict(id=name,weight=weight,status=status,reason=reason))
    def yes(value): return 'PASS' if value else 'FAIL'
    def unseen(name,weight,reason): check(name,weight,'UNVERIFIED',reason)
    video=directory/'video'; video.mkdir(exist_ok=True)
    content=(directory/'animation.html').read_text()
    url='https://animation.invalid/'
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,chromium_sandbox=True,
            args=['--disable-background-networking','--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        context=browser.new_context(viewport={'width':1280,'height':720},device_scale_factor=1,
            service_workers='block',accept_downloads=False,record_video_dir=str(video),
            record_video_size={'width':1280,'height':720})
        browser_version=browser.version
        delivered=False
        def route(r):
            nonlocal delivered
            request=r.request
            if not delivered and request.url==url and request.is_navigation_request():
                delivered=True
                r.fulfill(status=200,content_type='text/html',body=content,headers={
                    'Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; worker-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'"})
            else:
                blocked.append({'url':request.url,'type':request.resource_type}); r.abort()
        context.route('**/*',route)
        context.route_web_socket('**/*',lambda ws: (blocked.append({'type':'websocket','url':ws.url}),ws.close()))
        page=context.new_page()
        page.set_default_timeout(2500)
        page.on('pageerror',lambda e: errors.append(str(e)))
        page.on('console',lambda msg: errors.append(msg.text) if msg.type=='error' else None)
        page.on('dialog',lambda d:d.dismiss())
        page.on('download',lambda d:d.cancel())
        context.on('page',lambda new:new.close() if new!=page else None)
        try:
            page.goto(url,wait_until='load',timeout=8000)
            page.wait_for_timeout(200)
            stage=page.locator('#animation-stage')
            toggle=page.get_by_test_id('toggle-play')
            restart=page.get_by_test_id('restart')
            state=page.get_by_test_id('playback-state')
            def snap(): return stage.screenshot(animations='allow',timeout=2500)
            def status(): return state.inner_text().strip()
            def end_time(begin):
                while time.monotonic()-begin<32.5:
                    if status()=='ended': return time.monotonic()-begin
                    page.wait_for_timeout(100)
                return None
            loaded=stage.count()==1 and stage.is_visible() and toggle.is_visible() and restart.is_visible()
            check('load',15,yes(loaded),'Browser loaded frozen HTML; stage and controls visible')
            if not loaded: raise ValueError('Required visible stage/controls missing')
            toggle.click(); page.wait_for_timeout(350)
            first=snap(); page.wait_for_timeout(600); moving=snap()
            toggle.click(); page.wait_for_timeout(100); paused=snap(); paused_state=status()
            page.wait_for_timeout(500); paused2=snap()
            (directory/'paused.png').write_bytes(paused)
            measurements['pause_pixel_mean_delta']=delta(paused,paused2)
            measurements['early_motion_pixel_mean_delta']=delta(first,moving)
            check('pause_pixels',10,yes(paused_state=='paused' and delta(paused,paused2)<=0.05),
                  'Stage pixels compared across 500 ms after pause; tolerance mean RGB delta <= 0.05')
            toggle.click(); page.wait_for_timeout(300)
            ok=status()=='playing'
            for _ in range(5):
                toggle.click(); ok=ok and status()=='paused'
                page.wait_for_timeout(70)
                toggle.click(); ok=ok and status()=='playing'
                page.wait_for_timeout(70)
            check('control_states',10,yes(ok),'Five pause/resume cycles; state labels only')
            unseen('resume_continuity',5,'State and pixels do not prove semantic timeline continuity')
            restart.click(); begin=time.monotonic(); page.wait_for_timeout(150)
            reset1=snap(); (directory/'restart.png').write_bytes(reset1)
            duration1=end_time(begin)
            measurements['first_observed_play_seconds']=duration1
            check('duration',10,yes(duration1 is not None and 29<=duration1<=31.2),
                  'Real wall-time until ended after restart; 0.2 s observation tolerance')
            if duration1 is not None:
                (directory/'ended.png').write_bytes(snap())
                restart.click(); begin=time.monotonic(); page.wait_for_timeout(150)
                reset2=snap(); duration2=end_time(begin)
                measurements.update(replay_seconds=duration2,restart_pixel_delta=delta(reset1,reset2))
                # Reaching ended twice is measured; reset likeness remains diagnostic because sampling jitter matters.
                check('repeat_duration',10,yes(duration2 is not None and 29<=duration2<=31.2),'Second complete replay duration; event correctness not inferred')
            else:
                check('repeat_duration',10,'UNVERIFIED','No first end state; second full replay not evaluated')
            unseen('story_replay',5,'Key events and reset correctness require validated temporal visual evaluator')
            def usable():
                for el in (stage,toggle,restart):
                    box=el.bounding_box()
                    if not box or box['width']<=0 or box['height']<=0 or box['x']<0 or box['y']<0 or box['x']+box['width']>page.viewport_size['width']+1 or box['y']+box['height']>page.viewport_size['height']+1:
                        return False
                toggle.click(trial=True); restart.click(trial=True)
                return True
            desktop=usable()
            page.set_viewport_size({'width':390,'height':844}); page.wait_for_timeout(150)
            small=usable()
            check('viewport',10,yes(desktop and small),'Stage and controls fit viewport; controls pass browser hit test at both dimensions')
        except Exception as e:
            measurements['execution_error']=str(e)
        finally:
            check('offline',15,yes(not blocked and not any('Content Security Policy' in e or 'violates' in e for e in errors)),
                  'External requests blocked and CSP violations recorded; only exercised paths covered')
            check('runtime_errors',5,yes(not errors and 'execution_error' not in measurements),'Observed browser errors and execution failures')
            unseen('resource_stability',5,'No calibrated resource thresholds for this hardware')
            expected={'load':15,'pause_pixels':10,'control_states':10,'resume_continuity':5,
                'duration':10,'repeat_duration':10,'story_replay':5,'viewport':10,'offline':15,'runtime_errors':5,'resource_stability':5}
            present={c['id'] for c in checks}
            for name,weight in expected.items():
                if name not in present: unseen(name,weight,'Not reached: '+measurements.get('execution_error','unknown'))
            context.close(); browser.close()
    result={'status':'DIAGNOSTIC','checks':checks,'errors':errors,'blocked_requests':blocked,
        'measurements':measurements,'browser':browser_version,'viewport':[1280,720],
        'video':'video/','video_fps':'Playwright native capture; not asserted to be standard 30 FPS',
        'duration_ms':round((time.monotonic()-started)*1000),
        'isolation':'new nonpersistent Chromium context, synthetic document route, CSP, blocked requests/websockets/popups/downloads',
        'limitations':['Browser isolation is not an OS security boundary','Motion pixels cannot prove characters, story, beauty or continuous animation','No validated semantic/aesthetic judge']}
    (directory/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__': verify(Path(sys.argv[1]).resolve())
