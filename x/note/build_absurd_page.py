#!/usr/bin/env python3
"""x/absurd.md から x/note/absurd-page.html を生成する。

理不尽一覧はここでしか持っていないので、md を直したら必ずこれを流し直すこと。
  python3 x/note/build_absurd_page.py
"""
import html, re, pathlib

SRC = pathlib.Path("x/absurd.md")
OUT = pathlib.Path("x/note/absurd-page.html")

MARKS = {
    "◆": ("k12", "型12 会話劇"),
    "■": ("k11", "型11 リスト"),
    "●": ("k18", "型1・型8"),
}

def parse():
    cat, items = None, []
    for line in SRC.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cat = line[3:].strip()
            continue
        m = re.match(r"- \*\*(R\d+)\*\*\s*([◆■●])\s*(.+)$", line)
        if m and cat:
            items.append({"id": m.group(1), "mark": m.group(2),
                          "cat": cat, "text": m.group(3).strip()})
    return items

def build(items):
    cats = []
    for it in items:
        if it["cat"] not in cats:
            cats.append(it["cat"])
    by_cat = {c: sum(1 for i in items if i["cat"] == c) for c in cats}
    by_mark = {k: sum(1 for i in items if i["mark"] == k) for k in MARKS}

    chips = ['<button class="chip on" data-cat="*">すべて<span class="n">%d</span></button>' % len(items)]
    for c in cats:
        chips.append('<button class="chip" data-cat="%s">%s<span class="n">%d</span></button>'
                     % (html.escape(c), html.escape(c), by_cat[c]))

    marks = ['<button class="chip mchip on" data-mark="*">型を問わない<span class="n">%d</span></button>' % len(items)]
    for sym, (cls, label) in MARKS.items():
        marks.append('<button class="chip mchip %s" data-mark="%s">%s %s<span class="n">%d</span></button>'
                     % (cls, sym, sym, html.escape(label), by_mark[sym]))

    rows = []
    for it in items:
        cls, label = MARKS[it["mark"]]
        q = it["id"] + it["text"] + it["cat"] + label
        rows.append(
            '<li class="row" data-cat="%s" data-mark="%s" data-q="%s">'
            '<span class="num">%s</span>'
            '<span class="txt">%s<span class="sub">%s</span></span>'
            '<span class="mk %s">%s</span></li>'
            % (html.escape(it["cat"]), it["mark"], html.escape(q),
               it["id"], html.escape(it["text"]), html.escape(it["cat"]), cls, html.escape(label)))

    return TPL.replace("{{N}}", str(len(items))) \
              .replace("{{N12}}", str(by_mark["◆"])) \
              .replace("{{N11}}", str(by_mark["■"])) \
              .replace("{{N18}}", str(by_mark["●"])) \
              .replace("{{CHIPS}}", "\n".join(chips)) \
              .replace("{{MARKS}}", "\n".join(marks)) \
              .replace("{{ROWS}}", "\n".join(rows))

TPL = r"""<title>上司の理不尽</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Shippori+Mincho+B1:wght@500;700&family=Zen+Kaku+Gothic+New:wght@400;500;700&family=Roboto+Mono:wght@400;500&display=swap">

<style>
/* ねこすけの常設ページと同じ地色・同じ三書体。一覧は行で読ませ、型の印だけ色を持たせる */
:root{
  --ground:#F0F1EC; --surface:#FFFFFF; --surface-2:#E7E9E2;
  --ink:#1A1D1A; --ink-2:#4A504A; --muted:#6E756C; --line:#D3D7CC;
  --accent:#24456B; --accent-soft:#DFE6EF;
  --flag:#A9382C; --flag-soft:#F0DFDC;
  --ok:#3F6B4A; --ok-soft:#DEE9E0;
  --warn:#7A5A1E; --warn-soft:#EFE6D2;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ground:#14171A; --surface:#1C2024; --surface-2:#242A2F;
    --ink:#E9EBE6; --ink-2:#BFC5BE; --muted:#939A93; --line:#31373C;
    --accent:#8FB3DE; --accent-soft:#1F2E40;
    --flag:#E08376; --flag-soft:#3A2523;
    --ok:#8FC49C; --ok-soft:#203024;
    --warn:#D9BC7C; --warn-soft:#332A17;
    color-scheme:dark;
  }
}
:root[data-theme="dark"]{
  --ground:#14171A; --surface:#1C2024; --surface-2:#242A2F;
  --ink:#E9EBE6; --ink-2:#BFC5BE; --muted:#939A93; --line:#31373C;
  --accent:#8FB3DE; --accent-soft:#1F2E40;
  --flag:#E08376; --flag-soft:#3A2523;
  --ok:#8FC49C; --ok-soft:#203024;
  --warn:#D9BC7C; --warn-soft:#332A17;
  color-scheme:dark;
}
*{box-sizing:border-box;}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:"Zen Kaku Gothic New","Hiragino Sans","Noto Sans JP",system-ui,sans-serif;
  font-size:16px;line-height:1.8;-webkit-font-smoothing:antialiased;}
.wrap{max-width:760px;margin:0 auto;padding-inline:20px;padding-block:0 72px;}

.masthead{padding-block:52px 18px;display:flex;flex-direction:column;gap:12px;}
.eyebrow{font-family:"Roboto Mono",monospace;font-size:11.5px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--muted);}
h1{font-family:"Shippori Mincho B1",serif;font-weight:700;margin:0;
  font-size:clamp(30px,6vw,44px);line-height:1.25;letter-spacing:.02em;text-wrap:balance;}
.lede{margin:0;max-width:60ch;color:var(--ink-2);font-size:15px;}

.legend{display:flex;flex-wrap:wrap;gap:8px;margin-top:4px;}
.lg{font-family:"Roboto Mono",monospace;font-size:11.5px;padding:3px 9px;border-radius:2px;
  border:1px solid var(--line);white-space:nowrap;}
.lg.k12{background:var(--accent-soft);color:var(--accent);border-color:transparent;}
.lg.k11{background:var(--ok-soft);color:var(--ok);border-color:transparent;}
.lg.k18{background:var(--warn-soft);color:var(--warn);border-color:transparent;}

.guard{margin-top:24px;background:var(--surface);border:1px solid var(--line);
  border-left:3px solid var(--flag);border-radius:3px;padding:16px 18px;}
.guard h3{font-family:"Shippori Mincho B1",serif;font-size:16px;margin:0 0 8px;}
.guard ul{margin:0;padding-left:1.1em;}
.guard li{font-size:14px;color:var(--ink-2);margin-bottom:4px;}
.guard li b{color:var(--ink);}

.tools{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;
  background:var(--ground);padding-block:14px 10px;margin-top:28px;
  border-bottom:1px solid var(--line);}
.search{display:flex;align-items:center;gap:10px;}
#q{flex:1;min-width:0;font:inherit;font-size:15px;color:var(--ink);
  background:var(--surface);border:1px solid var(--line);border-radius:3px;
  padding:9px 12px;}
#q::placeholder{color:var(--muted);}
.hit{font-family:"Roboto Mono",monospace;font-size:12px;color:var(--muted);
  flex:0 0 auto;font-variant-numeric:tabular-nums;}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px;}
.chip{font:inherit;font-size:13px;color:var(--ink-2);cursor:pointer;
  background:var(--surface);border:1px solid var(--line);border-radius:999px;
  padding:4px 11px;display:inline-flex;align-items:center;gap:6px;}
.chip .n{font-family:"Roboto Mono",monospace;font-size:11px;color:var(--muted);
  font-variant-numeric:tabular-nums;}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff;}
.chip.on .n{color:rgba(255,255,255,.72);}
.mrow{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px;}

.list{list-style:none;margin:0;padding:0;}
.row{display:flex;gap:12px;align-items:baseline;padding:11px 2px;
  border-bottom:1px solid var(--line);}
.num{font-family:"Roboto Mono",monospace;font-size:12px;color:var(--muted);
  flex:0 0 auto;width:3.1em;font-variant-numeric:tabular-nums;}
.txt{flex:1;min-width:0;font-size:15.5px;line-height:1.65;}
.sub{display:block;font-size:12px;color:var(--muted);margin-top:2px;}
.mk{flex:0 0 auto;font-family:"Roboto Mono",monospace;font-size:10.5px;
  padding:2px 7px;border-radius:2px;white-space:nowrap;align-self:center;}
.mk.k12{background:var(--accent-soft);color:var(--accent);}
.mk.k11{background:var(--ok-soft);color:var(--ok);}
.mk.k18{background:var(--warn-soft);color:var(--warn);}
.row[hidden]{display:none;}
.empty{padding:40px 0;color:var(--muted);font-size:14.5px;text-align:center;}

.howto{margin-top:34px;background:var(--surface);border:1px solid var(--line);
  border-radius:3px;padding:18px 20px;}
.howto h3{font-family:"Shippori Mincho B1",serif;font-size:17px;margin:0 0 10px;}
.howto ol{margin:0;padding-left:1.2em;}
.howto li{margin-bottom:6px;font-size:14.5px;color:var(--ink-2);}
.howto li b{color:var(--ink);}
.howto p{margin:12px 0 0;font-size:13.5px;color:var(--muted);}
.howto p b{color:var(--ink);}

footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);
  font-family:"Roboto Mono",monospace;font-size:11.5px;color:var(--muted);line-height:1.9;}
a{color:var(--accent);}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important;}}
</style>

<div class="wrap">

<header class="masthead">
  <p class="eyebrow">あるある系の素材帳</p>
  <h1>上司の理不尽</h1>
  <p class="lede">ネタ単位ではなく「理不尽そのもの」を貯める場所です。セリフや場面はまだ付けません。ここから会話劇・箇条書きリスト・上司の一言が生えます。</p>
  <div class="legend">
    <span class="lg k12">◆ 型12 会話劇 {{N12}}</span>
    <span class="lg k11">■ 型11 リスト {{N11}}</span>
    <span class="lg k18">● 型1・型8 {{N18}}</span>
  </div>
</header>

<div class="guard">
  <h3>入れないもの</h3>
  <ul>
    <li><b>暴言・人格否定・ハラスメント。</b>笑いにならず、炎上します</li>
    <li><b>特定の会社や個人が割れるもの</b></li>
    <li><b>自分の職場でしか起きないこと。</b>「どこにでもある理不尽」がこの帳面の線です</li>
  </ul>
</div>

<div class="tools">
  <div class="search">
    <input id="q" type="search" placeholder="理不尽を検索（例: 会議 / 有給 / R41）" aria-label="理不尽を検索">
    <span class="hit" id="hit"></span>
  </div>
  <div class="chips" id="chips">
{{CHIPS}}
  </div>
  <div class="mrow" id="marks">
{{MARKS}}
  </div>
</div>

<ul class="list" id="list">
{{ROWS}}
</ul>
<p class="empty" id="empty" hidden>そのことばでは見つかりませんでした。</p>

<div class="howto">
  <h3>ここからポストにする</h3>
  <ol>
    <li><b>◆から会話劇をつくる。</b>上司の2文に落として、3つで検品する — 同じ息で続けて出るか／1枚の絵の中で起きるか／返しは短く素直か</li>
    <li><b>■は7〜11個まとめて箇条書きリストに。</b>字数は4字から1字ずつの階段、毒は最後に置く</li>
    <li><b>●は場面と決め台詞が立つ</b>ので、上司の一言か短文に回す</li>
  </ol>
  <p><b>全部を上司批判にしないこと。</b>このアカウントは元リクのマネージャーで、いまも面接官です。理不尽を並べるだけだと、ただの愚痴アカウントになります。<b>3〜4本に1本は「判定する側」の投稿</b>を挟んでください。</p>
</div>

<footer>
  元データは x/absurd.md　—　全{{N}}件<br>
  更新は build_absurd_page.py を流し直して同じURLに publish
</footer>

</div>

<script>
(function(){
  var rows=[].slice.call(document.querySelectorAll('.row'));
  var q=document.getElementById('q'), hit=document.getElementById('hit');
  var empty=document.getElementById('empty');
  var cat='*', mark='*';

  function apply(){
    var t=(q.value||'').trim().toLowerCase(), n=0;
    rows.forEach(function(r){
      var ok=(cat==='*'||r.dataset.cat===cat)
          && (mark==='*'||r.dataset.mark===mark)
          && (!t||r.dataset.q.toLowerCase().indexOf(t)>=0);
      r.hidden=!ok; if(ok)n++;
    });
    hit.textContent=n+' 件';
    empty.hidden=n>0;
  }
  function wire(id, key){
    document.getElementById(id).addEventListener('click', function(e){
      var b=e.target.closest('.chip'); if(!b)return;
      [].forEach.call(this.querySelectorAll('.chip'), function(c){c.classList.remove('on');});
      b.classList.add('on');
      if(key==='cat') cat=b.dataset.cat; else mark=b.dataset.mark;
      apply();
    });
  }
  wire('chips','cat'); wire('marks','mark');
  q.addEventListener('input', apply);
  apply();
})();
</script>
"""

if __name__ == "__main__":
    items = parse()
    OUT.write_text(build(items), encoding="utf-8")
    print(f"{OUT} — {len(items)}件")
