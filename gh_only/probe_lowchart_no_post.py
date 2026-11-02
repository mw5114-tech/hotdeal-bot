"""Live read-only Lowchart draft probe: no Daangn browser or post actions."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from gh_only.default_deal_automation import enrich_deal
from gh_only.daangn_editor_probe import read_one_draft


def main():
    state = json.loads(Path("state/daangn_jirum_manual_fifo.json").read_text("utf-8"))
    for row in state["pending"][-5:]:
        post = {
            "source_message_id": row["id"],
            "source_post_url": row["source_post_url"],
            "source_text": row["source_text"],
            "original_purchase_url": row["original_purchase_url"],
            "title": row["title"],
            "sale_price_krw": row["sale_price_krw"],
        }
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            draft = enrich_deal(post, folder, owner_tag="AF9668915")
            print("LOWCHART_DRAFT_PROBE " + json.dumps({
                "id": row["id"],
                "identity_status": draft["product_identity"]["status"],
                "metadata_status": draft["image_metadata_status"],
                "original_title": row["source_text"].splitlines()[1][:110] if len(row["source_text"].splitlines())>1 else row["title"][:110],
                "lowchart_title": draft.get("og_title"),
                "image_source": draft["image_source"],
                "image_match": draft["image_product_match"],
                "photo_status": draft["photo"]["status"],
                "photo_bytes": draft["photo"].get("bytes", 0),
                "post_count": 0,
            }, ensure_ascii=False))
            if draft["photo"]["status"] == "IMAGE_SAVED":
                (folder / "drafts.json").write_text(json.dumps({
                    "schema": "github-jirum-default-price-photo-affsdp-draft-v2",
                    "drafts": [draft]
                }, ensure_ascii=False), "utf-8")
                article = read_one_draft(folder)
                print("EDITOR_DRAFT_VALID " + json.dumps({
                    "source_id": article["source_id"],
                    "local_image": article["image"].is_file(),
                    "post_count": 0,
                }))


if __name__ == "__main__":
    main()
