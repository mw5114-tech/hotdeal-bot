"""Read-only diagnose Lowchart validated price for one already-published deal.

No publication, no token/cookie printing; only titles and exact item source metadata.
"""
import json,os
from gh_only.default_deal_automation import resolve_identity,get_kakao_og,get_title_tokens,og_matches_product
from gh_only.default_deal_format import validated_history,same_named_option,normalized_product_name
from gh_only.lowchart_price_reference import fetch_one
from gh_only.pipeline import enrich_posts,fetch_public_html

def main():
    posts=enrich_posts(fetch_public_html(),max_posts=30)
    item=next((p for p in posts if p.get("source_message_id")==94738),None)
    if item is None:
        print("LOWCHART_DIAGNOSTIC "+json.dumps({"status":"SOURCE_NOT_VISIBLE"}))
        return
    identity=resolve_identity(item["original_purchase_url"])
    og=get_kakao_og(item["original_purchase_url"],os.getenv("KAKAO_DEV_STORAGE_STATE_JSON",""))
    p,i=identity.get("product_id"),identity.get("item_id")
    result={"source_id":94738,"identity_status":identity.get("status"),
            "product_id":p,"item_id":i,
            "kakao_status":og.get("status"),"og_title":og.get("og_title"),
            "og_matches_product":og_matches_product(og,identity)}
    if p and i:
        ref=fetch_one(p,i,get_title_tokens(og.get("og_title")),item["sale_price_krw"])
        result["history"]={k:ref.get(k) for k in (
            "reason","source_url","page_title","source_item_identifiers_present",
            "title_option_matches","usable_historical_reference","observed_average_krw",
            "official_normal_price","product_id","item_id","http_status")}
        result["full_title_normalized_match"]=same_named_option(og.get("og_title"),ref.get("page_title"))
        result["og_normalized"]=normalized_product_name(og.get("og_title"))
        result["lowchart_normalized"]=normalized_product_name(ref.get("page_title"))
        result["accepted_average"]=validated_history(ref,p,i,og.get("og_title"))
    print("LOWCHART_DIAGNOSTIC "+json.dumps(result,ensure_ascii=False))

if __name__=="__main__":main()
