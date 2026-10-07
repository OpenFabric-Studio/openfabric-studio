"""Reviewed dataset identities separate held-out references from training."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from app.video_contracts import CharacterDatasetReview, CharacterDatasetItem


class TrainingReviewTests(unittest.TestCase):
    def test_effective_recipe_is_recorded_only_for_the_builtin_trainer(self) -> None:
        from app.video_training_review import build_provenance
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / f'{index}.png' for index in range(4)]
            for index, path in enumerate(paths):
                path.write_bytes(str(index).encode())
            review = CharacterDatasetReview(reviewed=True, items=[CharacterDatasetItem(upload_index=index,
                caption='A portrait', role='held_out' if index == 3 else 'training') for index in range(4)])
            builtin = build_provenance(root, paths, ['photo'] * 4, review, builtin=True)
            self.assertIsNotNone(builtin.recipe)
            if builtin.recipe is None:
                self.fail('Missing effective recipe')
            self.assertEqual((builtin.recipe.width, builtin.recipe.height, builtin.recipe.frames), (960, 544, 97))
            self.assertEqual(builtin.recipe.memory_mode, 'low_ram')
            custom = build_provenance(root, paths, ['photo'] * 4, review)
            self.assertIsNone(custom.recipe)
            self.assertNotEqual(custom.settings_sha256, builtin.settings_sha256)

    def test_review_identity_changes_with_caption_settings_or_data_and_rejects_leakage(self) -> None:
        from app.video_training_review import build_provenance
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for index in range(4):
                path = root / f'{index}.png'
                path.write_bytes(f'photo-{index}'.encode())
                paths.append(path)
            review = CharacterDatasetReview(reviewed=True, items=[CharacterDatasetItem(upload_index=index,
                caption=f'Character view {index}', role='held_out' if index == 3 else 'training') for index in range(4)])
            first = build_provenance(root, paths, ['photo'] * 4, review)
            review.items[0].caption = 'A changed pose'
            second = build_provenance(root, paths, ['photo'] * 4, review)
            self.assertNotEqual(first.dataset_sha256, second.dataset_sha256)
            review.settings.steps = 100
            third = build_provenance(root, paths, ['photo'] * 4, review)
            self.assertNotEqual(second.settings_sha256, third.settings_sha256)
            paths[3].write_bytes(paths[0].read_bytes())
            with self.assertRaisesRegex(ValueError, 'held_out_overlap'):
                build_provenance(root, paths, ['photo'] * 4, review)
