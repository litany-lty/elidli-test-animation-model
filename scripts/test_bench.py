"""Focused invariants for the development benchmark, not generated artwork."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

spec=importlib.util.spec_from_file_location('bench',Path(__file__).with_name('bench.py'))
b=importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
original_boot_id=b.boot_id


def materials():
    return [{'id':str(i),'name':f'用户素材{i}','type':'profile' if i==1 else 'text',
             'content':'https://example.invalid/user-account' if i==1 else f'用户自定义素材{i}',
             'requirement':'用于动画，必须保留用户设定'} for i in range(1,5)]


def pack():
    return {'characters':[dict(character_id=str(i),source_urls=[],
        retrieved_at=None,evidence_type='unavailable',observed_features=[],uncertainties=['Fixture: source unavailable'],
        adopted_description='Explicitly fictional fixture character',fictional_additions=['fictional'],status='UNAVAILABLE',evidence_ids=[],
        avatar={'url':None,'viewed':False,'observations':[],'evidence_ids':[]},recent_posts=[],inference_notes=[])
        for i in range(1,5)]}


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.run=self.root/'run'
        self.materials_file=self.root/'materials.json'; self.materials_file.write_text(json.dumps(materials()))
        # These tests exercise recorder logic, independently of host sysctl permissions.
        boot=patch.object(b,'boot_id',return_value='test-boot-domain'); boot.start(); self.addCleanup(boot.stop)
    def tearDown(self): self.temp.cleanup()
    def call(self,*args):
        with patch.object(sys,'argv',['bench',*map(str,args)]),contextlib.redirect_stdout(io.StringIO()) as out:
            b.main()
        return out.getvalue()
    def prepare(self):
        self.call('issue','--run',self.run,'--materials',self.materials_file,'--exposed')
        src=self.root/'sources.json'; src.write_text(json.dumps(pack()))
        self.call('sources','--run',self.run,'--file',src)
        design=self.root/'design.md'; design.write_text('Fixture design. No model artwork or scores. <script>alert(1)</script>')
        prompt=self.root/'creative-prompt.md'; prompt.write_text('Four characters repair a bridge. A aligns a prism; B controls a light rail. Their appearances must be independently derived.')
        self.call('design','--run',self.run,'--file',design,'--prompt',prompt)
        self.art=self.root/'animation.html'; self.art.write_text('<html>fixture</html>')
        self.call('submit','--run',self.run,'--file',self.art)
    def test_frozen_original_and_authorized_repair(self):
        self.prepare()
        with self.assertRaises(ValueError): self.call('submit','--run',self.run,'--file',self.art)
        self.art.write_text('repair')
        self.call('repair-start','--run',self.run,'--note','Fixture explicit user request')
        self.call('submit','--run',self.run,'--file',self.art)
        self.assertEqual((self.run/'submissions/attempt-01/animation.html').read_text(),'<html>fixture</html>')
        self.assertEqual((self.run/'submissions/attempt-02/animation.html').read_text(),'repair')
        m=json.loads((self.run/'manifest.json').read_text())
        self.assertIsNotNone(m['attempts'][1]['repair_ms'])
    def test_exports_are_self_contained_roundtrip_and_no_fake_scores(self):
        self.prepare(); self.call('report','--run',self.run)
        for name,mode in [('reuse-test','live-brief'),('reuse-original','live-elements'),('reuse-elements','frozen-elements'),('reuse-design','frozen-design')]:
            data=(self.run/f'exports/{name}.md').read_text(); self.assertEqual(b.parse_packet(data)[0],mode)
            child=self.root/name
            self.call('issue','--run',child,'--packet',self.run/f'exports/{name}.md')
            self.assertEqual(json.loads((child/'manifest.json').read_text())['timing_scope'],'first_tool_after_user_material_collection')
        scores=json.loads((self.run/'submissions/attempt-01/scores.json').read_text())
        self.assertIsNone(scores['aesthetics']['score']); self.assertIsNone(scores['programming']['score'])
        self.assertIn('素材推导未完成',(self.run/'results/report.md').read_text())
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',(self.run/'results/report.html').read_text())
    def test_incomplete_packet_rejected(self):
        text=b.packet(b.task_packet(materials()),'frozen-elements',pack())
        with self.assertRaises(ValueError): b.parse_packet(text.replace('六、动画制作要求',''))
    def test_hash_detects_modification(self):
        self.prepare(); p=self.run/'submissions/attempt-01/animation.html'; p.chmod(0o644); p.write_text('changed')
        with self.assertRaises(ValueError): self.call('report','--run',self.run)
    def test_reboot_and_clock_discontinuity_not_fabricated(self):
        a=b.stamp(); c=dict(a); c['boot']='different'
        self.assertIsNone(b.elapsed(a,c))
        c=dict(a); c['mono_ns']+=10_000_000_000
        self.assertIsNone(b.elapsed(a,c))
    def test_timeout_keeps_original(self):
        self.prepare(); m=json.loads((self.run/'manifest.json').read_text()); original=m['attempts'][0]
        a=b.stamp(); m['issued']=dict(a); m['issued']['mono_ns']-=901_000_000_000
        m['issued']['utc']=(b.dt.datetime.fromisoformat(a['utc'])-b.dt.timedelta(seconds=901)).isoformat()
        b.write_json(self.run/'manifest.json',m)
        self.call('repair-start','--run',self.run,'--note','Fixture')
        self.call('submit','--run',self.run,'--file',self.art)
        m=json.loads((self.run/'manifest.json').read_text())
        self.assertEqual(m['attempts'][1]['budget_status'],'TIMEOUT')
        self.assertEqual(m['attempts'][0],original)
    def test_late_evidence_cannot_be_adopted(self):
        self.call('issue','--run',self.run,'--materials',self.materials_file)
        m=json.loads((self.run/'manifest.json').read_text()); m['evidence']=[{'id':1,'late':True}]
        b.write_json(self.run/'manifest.json',m)
        source=pack(); source['characters'][0]['evidence_ids']=[1]
        p=self.root/'sources.json'; p.write_text(json.dumps(source))
        with self.assertRaises(ValueError): self.call('sources','--run',self.run,'--file',p)

    def test_primary_reuse_does_not_copy_inferred_portraits_or_design(self):
        self.prepare(); self.call('report','--run',self.run)
        p=self.run/'exports/reuse-test.md'; text=p.read_text()
        self.assertNotIn('Explicitly fictional fixture character',text)
        self.assertNotIn('Fixture design.',text)
        self.assertNotIn('BEGIN_FROZEN_SOURCES',text)
        self.assertEqual(b.creative_prompt(text),(self.root/'creative-prompt.md').read_text())
        child=self.root/'reused'
        self.call('issue','--run',child,'--packet',p)
        m=json.loads((child/'manifest.json').read_text())
        self.assertEqual(m['phase'],'research'); self.assertNotIn('sources',m)
        self.assertEqual(b.material_requirements(text),materials())
        self.assertEqual(m['designs'],[])
        self.assertEqual(b.verified(child,m['creative_prompt']).decode(),(self.root/'creative-prompt.md').read_text())
    def test_bio_only_cannot_be_claimed_acquired(self):
        source=pack();source['characters'][0]['status']='ACQUIRED'
        with self.assertRaises(ValueError): b.validate_sources(source,materials())
    def test_avatar_and_recent_post_requirement(self):
        source=pack(); c=source['characters'][0]
        c.update(status='ACQUIRED',retrieved_at='2026-10-28T09:00:00+00:00',evidence_ids=[1,2],
                 avatar={'url':'https://example.invalid/avatar.png','viewed':True,'observations':['Illustrated blue circle'],'evidence_ids':[1]},
                 recent_posts=[{'url':'https://example.invalid/post','published_at':'2026-10-27T09:00:00+00:00','observations':['Discusses star maps'],'evidence_ids':[2]}],
                 inference_notes=['Blue avatar + star-map post -> fictional blue navigator outfit'])
        b.validate_sources(source,materials())
        c['recent_posts'][0]['published_at']='2026-08-28T09:00:00+00:00'
        with self.assertRaises(ValueError): b.validate_sources(source,materials())
    def test_historical_report_not_rewritten(self):
        self.prepare();m=json.loads((self.run/'manifest.json').read_text());m['protocol']='animation-model-test/dev-0.1'
        b.write_json(self.run/'manifest.json',m)
        with self.assertRaises(ValueError): self.call('report','--run',self.run)
        self.assertFalse((self.run/'results/report.html').exists())
    def test_export_reuse_is_complete_without_run(self):
        p=self.root/'prompt.md';p.write_text('Build one animation with four characters; independently infer A and B.')
        out=self.root/'reuse.md';self.call('export-reuse','--prompt',p,'--output',out,'--materials',self.materials_file)
        self.assertEqual(b.parse_packet(out.read_text())[0],'live-brief')
        with self.assertRaises(ValueError): b.parse_packet(out.read_text().replace('近期推文','简介'))

    def test_missing_materials_does_not_create_or_start_run(self):
        with self.assertRaises(ValueError): self.call('issue','--run',self.run)
        self.assertFalse(self.run.exists())
        self.materials_file.write_text(json.dumps(materials()[:3]))
        with self.assertRaises(ValueError): self.call('issue','--run',self.run,'--materials',self.materials_file)
        self.assertFalse(self.run.exists())
    def test_text_material_needs_no_avatar(self):
        mat=materials(); mat[0]['type']='text'; mat[0]['content']='用户自己的角色描述'
        source=pack()
        for c in source['characters']:
            c.update(status='ACQUIRED',uncertainties=[])
            for k in ('avatar','recent_posts','inference_notes'): c.pop(k)
        b.validate_sources(source,mat)
    def test_image_export_and_reissue_preserve_original_bytes(self):
        image=self.root/'reference.png'; image.write_bytes(b'fixture-image-bytes')
        mat=materials();mat[1].update(type='image',content=str(image))
        self.materials_file.write_text(json.dumps(mat))
        self.prepare(); self.call('report','--run',self.run)
        text=(self.run/'exports/reuse-test.md').read_text(); exported=b.material_requirements(text)[1]
        self.assertNotIn(str(self.root),text)
        self.assertEqual((self.run/'exports'/exported['content']).read_bytes(),image.read_bytes())
        child=self.root/'image-reuse'
        self.call('issue','--run',child,'--packet',self.run/'exports/reuse-test.md')
        self.assertEqual((child/'input'/exported['content']).read_bytes(),image.read_bytes())
        (self.run/'exports'/exported['content']).write_bytes(b'changed')
        with self.assertRaises(ValueError): self.call('issue','--run',self.root/'bad-image','--packet',self.run/'exports/reuse-test.md')
        self.assertFalse((self.root/'bad-image').exists())

    def test_denied_boot_clock_is_unmeasured_not_fabricated(self):
        with patch.object(b.subprocess,'check_output',side_effect=PermissionError('denied')), patch.object(b.sys,'platform','darwin'):
            self.assertIsNone(original_boot_id())
        a=b.stamp(); a['boot']=None
        self.assertIsNone(b.elapsed(a,a))


if __name__=='__main__': unittest.main()
