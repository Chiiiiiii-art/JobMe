import os
import tempfile
from jobme.db import init_db, is_favorite, add_favorite, get_favorites, get_job_and_eval, save_job
from jobme.models import Job
from jobme.exporter import export_favorites_to_csv
from jobme.bot import FavoriteButton, build_forward_job_embed


def test_favorites_db_and_export():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = os.path.join(tmpdir, "test_jobs.db")
        test_csv = os.path.join(tmpdir, "test_fav.csv")
        init_db(test_db)

        # 1. Initially not favorite
        assert not is_favorite("cakeresume", "job123", db_path=test_db)

        # 2. Add favorite
        added = add_favorite(
            platform="cakeresume",
            job_id="job123",
            title="Senior Python Backend Engineer",
            company="Awesome AI Inc.",
            location="Taipei",
            salary="NT$ 100,000 - 150,000 / month",
            url="https://example.com/job123",
            total_score=88.5,
            details_json='[{"name": "Skill Match", "score": 25, "max": 25, "confidence": 0.9}]',
            db_path=test_db,
        )
        assert added is True
        assert is_favorite("cakeresume", "job123", db_path=test_db)

        # 3. Duplicate add returns False
        added_dup = add_favorite(
            platform="cakeresume",
            job_id="job123",
            title="Senior Python Backend Engineer",
            company="Awesome AI Inc.",
            location="Taipei",
            salary="NT$ 100,000 - 150,000 / month",
            url="https://example.com/job123",
            total_score=88.5,
            db_path=test_db,
        )
        assert added_dup is False

        # 4. Query favorites
        favs = get_favorites(db_path=test_db)
        assert len(favs) == 1
        assert favs[0]["job_id"] == "job123"
        assert favs[0]["total_score"] == 88.5

        # 5. Export to CSV
        csv_path = export_favorites_to_csv(favs, test_csv)
        assert os.path.exists(csv_path)
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
            assert "Senior Python Backend Engineer" in content
            assert "Awesome AI Inc." in content

        # 6. Test forward embed builder
        embed = build_forward_job_embed(favs[0])
        assert "[88.5分] Senior Python Backend Engineer" in embed.title
        assert embed.color.value == 0x2ECC71  # Green color for >= 85

        # 7. Test remove favorite
        from jobme.db import remove_favorite
        removed = remove_favorite("cakeresume", "job123", db_path=test_db)
        assert removed is True
        assert not is_favorite("cakeresume", "job123", db_path=test_db)
        assert len(get_favorites(db_path=test_db)) == 0


def test_favorite_button_parsing():
    import re
    from jobme.bot import FavoriteButton, RemoveFavoriteButton
    from jobme.db import resolve_job_ref

    # 1. Compact ref_id creation and matching
    btn = FavoriteButton("cake", "3dd6fe45-21b3-4f22-b289-8ea56ca690c4-intern-ssd-manufacturing-dcf4d1a8a18a25ef017fdfd38e6fa0")
    assert len(btn.custom_id) <= 40, f"Button custom_id must be under 100, got {len(btn.custom_id)}"
    match = re.match(FavoriteButton.__discord_ui_compiled_template__, btn.custom_id)
    assert match is not None
    assert resolve_job_ref(match.group("key")) == ("cake", "3dd6fe45-21b3-4f22-b289-8ea56ca690c4-intern-ssd-manufacturing-dcf4d1a8a18a25ef017fdfd38e6fa0")

    # 2. Remove button
    rm_btn = RemoveFavoriteButton("cake", "3dd6fe45-21b3-4f22-b289-8ea56ca690c4-intern-ssd-manufacturing-dcf4d1a8a18a25ef017fdfd38e6fa0")
    assert len(rm_btn.custom_id) <= 40
    match_rm = re.match(RemoveFavoriteButton.__discord_ui_compiled_template__, rm_btn.custom_id)
    assert match_rm is not None

    # 3. Legacy format fallback
    match_leg = re.match(FavoriteButton.__discord_ui_compiled_template__, "jobme_fav:104:abc789")
    assert match_leg is not None


if __name__ == "__main__":
    test_favorites_db_and_export()
    test_favorite_button_parsing()
    print("ALL FAVORITE TESTS PASSED!")

