import contextlib,shutil,uuid
import contextlib,hashlib,io,json,tempfile,unittest,zipfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.package_public import allowed,build,candidates,scan

@contextlib.contextmanager
def test_directory():
    base=Path(__file__).resolve().parents[1]/'build'/'_public_tests'
    base.mkdir(parents=True,exist_ok=True)
    folder=base/uuid.uuid4().hex;folder.mkdir()
    try:yield str(folder)
    finally:
        if folder.resolve().is_relative_to(base.resolve()):shutil.rmtree(folder)


class PublicPackageTest(unittest.TestCase):
    def test_private_runtime_and_unlisted_material_are_excluded(self):
        with test_directory() as tmp:
            root=Path(tmp)
            for name,content in [('README.md','Public source'),('app/main.py','print(1)'),('data/schema.sql','-- schema'),('data/seed.py','# synthetic'),('data/private/key.dpapi','private'),('data/qbank.db','private'),('sources/paper.pdf','private'),('backup/old.zip','private'),('docs/photo.png','private'),('scripts/local-secret.py','private')]:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
            output=root/'dist/source.zip';build(root,output)
            with zipfile.ZipFile(output) as archive:
                names=set(archive.namelist());self.assertIn('question-bank/data/seed.py',names)
                self.assertFalse(any('qbank.db' in n or 'private/' in n or 'paper.pdf' in n or 'old.zip' in n or 'local-secret' in n for n in names))
                manifest=json.loads(archive.read('question-bank/PUBLIC_MANIFEST.json'))
                self.assertFalse(manifest['personal_database_included'])
                self.assertEqual(hashlib.sha256(archive.read('question-bank/README.md')).hexdigest(),manifest['files']['README.md']['sha256'])

    def test_sensitive_public_text_blocks_packaging_without_printing_values(self):
        with test_directory() as tmp:
            root=Path(tmp);secret='sk-'+'9'*32;(root/'README.md').write_text(secret)
            issues=scan(root);self.assertEqual(issues,[{'file':'README.md','rule':'api_key'}]);self.assertNotIn(secret,str(issues))
            with self.assertRaises(ValueError) as failure:build(root,root/'source.zip')
            self.assertNotIn(secret,str(failure.exception));self.assertFalse((root/'source.zip').exists())

    def test_external_symlink_is_refused(self):
        with test_directory() as tmp:
            root=Path(tmp)/'public';root.mkdir();outside=Path(tmp)/'other';outside.write_text('private')
            try:(root/'README.md').symlink_to(outside)
            except OSError:self.skipTest('Symlinks unavailable')
            with self.assertRaises(ValueError):candidates(root)

    def test_existing_archive_and_traversal_are_refused(self):
        self.assertFalse(allowed('../data/seed.py'));self.assertFalse(allowed('data/../app/main.py'))
        with test_directory() as tmp:
            root=Path(tmp);(root/'README.md').write_text('public');output=root/'archive.zip';output.write_bytes(b'keep')
            with self.assertRaises(ValueError):build(root,output)
            self.assertEqual(output.read_bytes(),b'keep')

if __name__ == '__main__':
    unittest.main()
