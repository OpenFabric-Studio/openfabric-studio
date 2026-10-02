# Seed-VC compatibility source

`seed_vc_inference.txt` retains the inspected source regions needed by compatibility regression tests, from [Plachta/Seed-VC](https://github.com/Plachta/Seed-VC), revision `51383efd921027683c89e5348211d93ff12ac2a8`, `inference.py`. These vendor excerpts, including the matching before/after regions in `../seed_vc_compat.py`, retain Seed-VC's GPL version 3 license. The license text is in `COPYING.seed-vc`. The rest of the fixture is test scaffolding; the repository's MIT license does not replace the vendor excerpt license.

Tests intentionally retain the inspected regions independently of the patch definitions. No model code is imported or executed by loading this fixture.
