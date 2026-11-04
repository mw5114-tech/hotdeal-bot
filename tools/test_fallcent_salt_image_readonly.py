"""Read-only Fallcent source-product/photo probe, no posting or secrets."""
from bs4 import BeautifulSoup
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener
from urllib.error import HTTPError
from pathlib import Path
import json, re, requests, sys
from gh_only.shortlink_probe import one_hop
slug="26년 신안 천일염 햇소금10kg 태평염전 간수뺀"
link="https://link.coupang.com/a/ioWODkPbz2"
try:
  redir=one_hop(link)
except Exception as ex:
  redir={"result":"ERROR", "type":type(ex).__name__}
print("COUPANG_LINK",json.dumps(redir,ensure_ascii=False),flush=True)
agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
urls=[
 "https://fallcent.com/product/search/?"+urlencode({"keyword":slug}),
 "https://fallcent.com/product/search/?"+urlencode({"keyword":"태평염전 간수뺀 햇소금 10kg"}),
 "https://fallcent.com/product/search/?"+urlencode({"keyword":"신안 천일염 햇소금10kg"}),
]
if redir.get("product_id") and redir.get("item_id"):
 urls.insert(0,"https://fallcent.com/product/?"+urlencode({"product_id":redir["product_id"],"item_id":redir["item_id"]}))
s=requests.Session()
s.headers.update({"User-Agent":agent,"Accept-Language":"ko-KR,ko;q=0.9","Accept":"text/html,application/xhtml+xml"})
paths=[]
for ui,u in enumerate(urls):
 try:
  r=s.get(u,timeout=20,allow_redirects=False)
  print("FALLCENT_HTTP",ui,r.status_code,r.url[:230],"bytes",len(r.content),"redirect",r.headers.get("Location","")[:120],flush=True)
  if r.status_code != 200:continue
  soup=BeautifulSoup(r.text,"html.parser")
  h=soup.find("h1")
  title=h.get_text(" ",strip=True) if h else soup.title.get_text(" ",strip=True) if soup.title else ""
  print("PAGE_TITLE",ui,title[:250],flush=True)
  # Exact text and the nearest surrounding product cards; do not disclose private data.
  plain=soup.get_text(" ",strip=True)
  for match in list(re.finditer(r"26년|2026년|햇소금|태평염전",plain))[:9]:
   print("TEXT_MATCH",ui,plain[max(0,match.start()-95):match.end()+135][:260],flush=True)
  metas=[]
  for m in soup.find_all("meta"):
   if m.get("property") in ("og:title","og:image","og:description") or m.get("name") in ("twitter:image",):
    metas.append([m.get("property") or m.get("name"),(m.get("content") or "")[:400]])
  print("OG_META",ui,json.dumps(metas,ensure_ascii=False)[:1500],flush=True)
  cards=[]
  for a in soup.find_all("a",href=True):
   txt=a.get_text(" ",strip=True)
   if ("소금" in txt or "염전" in txt) and "10kg" in txt.lower():
    images=[img.get("src") or img.get("data-src") for img in a.find_all("img")]
    cards.append({"title":txt[:240],"href":a["href"][:250],"images":[x[:250] for x in images if x][:2]})
  print("PRODUCT_ANCHORS",ui,json.dumps(cards[:15],ensure_ascii=False)[:11000],flush=True)
  if ui==0 and redir.get("product_id") and redir.get("item_id"):
   exact_title=("26년" in title or "2026" in title) and "10kg" in title.replace(" ","").lower() and ("태평염전" in title or "신안" in title)
   if exact_title:
    images=[m[1] for m in metas if m[0] in ("og:image","twitter:image")]
    for src in images[:1]:
     if src.startswith("https://"):
      try:
       ir=s.get(src,timeout=25)
       ct=ir.headers.get("content-type","")
       valid=ir.status_code==200 and len(ir.content)>1000 and len(ir.content)<2500000 and (ir.content.startswith(b'\xff\xd8\xff') or ir.content.startswith(b'\x89PNG\r\n\x1a\n') or ir.content.startswith(b'RIFF'))
       print("IMAGE_DOWNLOAD",ir.status_code,ct,len(ir.content),"valid",valid,"host",urlsplit(src).hostname,flush=True)
       if valid:
        suffix=".jpg" if ir.content.startswith(b'\xff\xd8\xff') else ".png" if ir.content.startswith(b'\x89PNG\r\n\x1a\n') else ".webp"
        Path("fallcent-photo").mkdir(exist_ok=True)
        fp=Path("fallcent-photo")/("salt-2026-shinan-10kg"+suffix)
        fp.write_bytes(ir.content)
        print("PHOTO_FILE",str(fp),"bytes",fp.stat().st_size,flush=True)
      except Exception as e: print("IMAGE_ERROR",type(e).__name__,flush=True)
 except Exception as e:
  print("FALLCENT_ERROR",ui,type(e).__name__,str(e)[:160],flush=True)
