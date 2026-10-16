"""Publish only existing, authentic BOP! production test exports into web/.

Run after using the app to export output/production/{bounce,fly,orbit}.{webm,svg,json}.
This tool never renders replacement media, runs inference, or starts a server.
"""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import shutil
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "production"
INPUT = ROOT / "input" / "production"
DESTINATION = ROOT / "web" / "production"

TAKES = (
    {"preset": "bounce", "number": "01", "title": "Big bounce", "tag": "BOP. BOP. BOING.",
     "asset": "bop-bounce.svg", "audio": "bop-bounce.wav", "duration": 7.2, "impacts": 7,
     "accent": "#d5fa60", "description": "짧게 두 번, 길게 한 번. 통통 튀는 소리를 캐릭터의 탄성으로 바꿉니다.",
     "cue": "0.35s · 1.00s · 1.72s / 리듬에 맞춘 7번의 어택"},
    {"preset": "fly", "number": "02", "title": "Lift off", "tag": "UP. HOLD. GO.",
     "asset": "bop-fly.svg", "audio": "bop-freeze-flight.wav", "duration": 7.2, "impacts": 3,
     "accent": "#ffad98", "description": "소리를 길게 올리고, 잠깐 쉽니다. 쉬는 동안 비행도 기다렸다 다시 이어집니다.",
     "cue": "2.30–3.15s / 무음에서는 경로 진행이 멈춥니다"},
    {"preset": "orbit", "number": "03", "title": "Cosmic groove", "tag": "ROUND. AND SOUND.",
     "asset": "bop-orbit.svg", "audio": "bop-orbit.wav", "duration": 7.6, "impacts": 5,
     "accent": "#c9b8ff", "description": "오르내리는 허밍 네 마디와 작은 끝음. 한 바퀴 도는 브랜드 범퍼가 됩니다.",
     "cue": "4개의 허밍 곡선 + 엔딩 액센트 / 5번의 어택"},
)


class GalleryError(ValueError):
    pass


def validate_sources() -> list[dict]:
    """Check the entire input set before writing any gallery files."""
    missing = []
    for take in TAKES:
        paths = [OUTPUT / f"{take['preset']}.{suffix}" for suffix in ("webm", "svg", "json")]
        paths += [INPUT / "assets" / take["asset"], INPUT / "audio" / take["audio"]]
        missing.extend(str(path.relative_to(ROOT)) for path in paths if not path.is_file())
        for path in paths:
            if path.exists() and not path.resolve().is_relative_to(ROOT.resolve()):
                raise GalleryError("A source resolves outside this project; refusing to copy it.")
    if missing:
        raise GalleryError("Export the production takes in the app first. Missing files:\n  " + "\n  ".join(missing))
    validated = []
    for take in TAKES:
        slug = take["preset"]
        try:
            project = json.loads((OUTPUT / f"{slug}.json").read_text(encoding="utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise GalleryError(f"{slug}.json is not a readable exported project.") from exc
        if project.get("format") != "voice-motion-project" or project.get("version") != 1:
            raise GalleryError(f"{slug}.json must be a Voice Motion Lab project export.")
        analysis = project.get("analysis", {})
        if project.get("settings", {}).get("preset") != slug:
            raise GalleryError(f"{slug}.json uses the wrong motion preset.")
        if analysis.get("engine") != "basic_pitch":
            raise GalleryError(f"{slug}.json was not analyzed with Basic Pitch; refusing an AI claim.")
        duration = analysis.get("duration")
        if not isinstance(duration, (float, int)) or abs(duration - take["duration"]) > .025:
            raise GalleryError(f"{slug}.json has an unexpected duration.")
        events = analysis.get("events", [])
        impacts = sum(isinstance(event, dict) and event.get("kind") == "impact" for event in events)
        if impacts != take["impacts"]:
            raise GalleryError(f"{slug}.json has {impacts} impacts; expected {take['impacts']}. Review the actual take.")
        with (OUTPUT / f"{slug}.webm").open("rb") as source:
            if source.read(4) != b"\x1aE\xdf\xa3":
                raise GalleryError(f"{slug}.webm is not a WebM/Matroska file.")
        if "<svg" not in (OUTPUT / f"{slug}.svg").read_text(encoding="utf-8-sig")[:1000]:
            raise GalleryError(f"{slug}.svg is not an SVG export.")
        with wave.open(str(INPUT / "audio" / take["audio"]), "rb") as source:
            audio_duration = source.getnframes() / source.getframerate()
            if abs(audio_duration - take["duration"]) > .025:
                raise GalleryError(f"{take['audio']} has an unexpected duration.")
        validated.append({**take, "duration": duration, "actual_impacts": impacts,
                          "engine": analysis["engine"], "sample_rate": analysis.get("sample_rate")})
    return validated


def copy_native(source: Path, relative_destination: str) -> dict:
    destination = DESTINATION / relative_destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    copied_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
    if source_hash != copied_hash:
        raise GalleryError("A native media copy failed its byte-for-byte hash check.")
    return {"path": relative_destination, "bytes": destination.stat().st_size, "sha256": copied_hash}


def card(take: dict) -> str:
    escape = html.escape
    slug, accent = take["preset"], take["accent"]
    return f"""<article class="take" id="{slug}" style="--accent:{accent}">
      <div class="take-heading"><span class="number">{take['number']}</span><span class="take-tag">{escape(take['tag'])}</span><span class="duration">{take['duration']:.1f}s</span></div>
      <div class="screen"><video controls playsinline preload="metadata" poster="takes/{slug}-poster.png" aria-label="BOP! {escape(take['title'])} — synthetic sound production test"><source src="takes/{slug}.webm" type="video/webm"><a href="takes/{slug}.webm">WebM 다운로드</a></video></div>
      <div class="take-body"><div class="title-row"><h2>{escape(take['title'])}</h2><span class="preset">{slug.upper()}</span></div>
      <p class="description">{escape(take['description'])}</p><p class="cue">{escape(take['cue'])}</p>
      <div class="facts"><span>{take['actual_impacts']} IMPACTS</span><span>BASIC PITCH · CPU</span><span>SYNTHETIC SOUND</span></div>
      <div class="downloads"><a class="primary" href="takes/{slug}.webm" download>영상 WebM <span aria-hidden="true">↗</span></a><a href="takes/{slug}.svg" download>모션 SVG</a><a href="takes/{slug}.json" download>편집용 JSON</a></div>
      <details><summary>원본 소스 · 제작 과정 보기</summary><div class="sources"><a href="assets/{escape(take['asset'])}" download>원본 SVG ↧</a><a href="audio/{escape(take['audio'])}" download>합성 퍼포먼스 WAV ↧</a></div><p>정지 SVG + 합성 오디오 → 앱에서 분석 → 독립 ComfyUI의 VoiceMotionAnalyze → Basic Pitch ONNX CPU → 공통 모션 렌더러 → SVG · WebM · JSON.</p><p>JSON에는 원본 벡터·오디오·분석·설정과 실제 변환 키프레임이 들어 있습니다. Studio에서 다시 열어 수정할 수 있습니다.</p></details>
      </div></article>"""


CSS = """
:root{color-scheme:dark;--ink:#111714;--cream:#f4f0e6;--muted:#a8afa6;--line:#343d35;--lime:#d5fa60;font-family:Arial,'Noto Sans KR','Malgun Gothic',sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--ink);color:var(--cream)}a{color:inherit;text-decoration:none}a:focus-visible,summary:focus-visible{outline:3px solid var(--lime);outline-offset:5px}a:hover{text-decoration:underline}header{max-width:1540px;margin:auto;padding:28px 5vw;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line)}.wordmark{font-size:27px;letter-spacing:-2px;font-weight:950}.wordmark span{display:inline-block;transform:rotate(-12deg);color:var(--lime)}nav{display:flex;align-items:center;gap:28px;font-size:12px;letter-spacing:.04em}.studio{border:1px solid #697260;border-radius:100px;padding:11px 18px}.hero{max-width:1540px;margin:auto;padding:68px 5vw 60px;position:relative}.eyebrow{display:flex;align-items:center;gap:11px;font-size:11px;font-weight:700;letter-spacing:.14em;color:var(--muted)}.eyebrow i{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--lime)}h1{font-size:clamp(48px,7.4vw,114px);font-weight:950;letter-spacing:-.07em;line-height:.94;margin:30px 0 27px}h1 span{color:var(--lime)}.hero-bottom{display:flex;justify-content:space-between;gap:30px;align-items:flex-end}.intro{font-size:15px;line-height:1.8;color:#c6ccc2;max-width:630px;margin:0}.stamp{border:1px solid var(--line);border-radius:9px;padding:15px 18px;min-width:205px;font-size:11px;color:var(--muted);line-height:1.7}.stamp strong{color:var(--cream);font-size:20px;letter-spacing:-.04em;font-weight:900}.collection{padding:0 5vw 65px;max-width:1540px;margin:auto}.section-label{display:flex;align-items:center;justify-content:space-between;border-top:1px solid var(--line);padding:17px 0 23px;font-size:10px;letter-spacing:.12em;color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}.take{background:var(--cream);color:var(--ink);border-radius:14px;overflow:hidden;box-shadow:0 7px 35px #0002}.take-heading{display:flex;align-items:center;gap:13px;padding:17px 18px;font-size:9px;letter-spacing:.09em;font-weight:800}.number{display:grid;place-items:center;border-radius:50%;background:var(--ink);color:var(--cream);width:27px;height:27px;font-size:10px}.take-tag{flex:1}.duration{color:#5b6559}.screen{background:#10151f;aspect-ratio:16/9;overflow:hidden;border-top:1px solid #deddd5;border-bottom:1px solid #deddd5}.screen video{width:100%;height:100%;display:block;object-fit:contain}.take-body{padding:23px 20px 20px}.title-row{display:flex;align-items:center;justify-content:space-between;gap:10px}h2{font-size:29px;letter-spacing:-.055em;margin:0;font-weight:900}.preset{background:var(--accent);border:1px solid #141b1422;border-radius:100px;padding:6px 9px;font-size:9px;font-weight:800;letter-spacing:.04em}.description{font-size:12px;line-height:1.8;min-height:65px;color:#465044;margin:17px 0 0;word-break:keep-all}.cue{font-size:10px;color:#646c5d;min-height:30px;line-height:1.6;margin:12px 0}.facts{display:flex;flex-wrap:wrap;gap:5px;margin:20px 0 19px}.facts span{border:1px solid #c7cec0;border-radius:4px;padding:6px 7px;font-size:8px;font-weight:700;letter-spacing:.03em}.downloads{display:grid;grid-template-columns:1.35fr 1fr 1fr;gap:7px;align-items:stretch}.downloads a{border:1px solid #b9c2af;border-radius:6px;font-size:10px;padding:11px 6px;text-align:center;font-weight:700;white-space:nowrap}.downloads a:hover{background:#dce3d3;text-decoration:none}.downloads .primary{background:var(--ink);color:var(--cream);border-color:var(--ink)}.primary span{margin-left:5px}details{border-top:1px solid #d5d9cc;margin-top:21px;padding-top:16px;font-size:10px;line-height:1.8;color:#56604f}summary{cursor:pointer;font-weight:700;color:#374330}.sources{display:flex;gap:15px;margin:13px 0 8px}.sources a{text-decoration:underline;text-underline-offset:3px}details p{margin:8px 0}.method{max-width:1540px;margin:auto;padding:5px 5vw 65px;display:grid;grid-template-columns:1fr 2fr;gap:38px}.method h2{font-size:28px;max-width:250px;line-height:1.12}.method p{font-size:12px;line-height:1.9;color:var(--muted);margin:0 0 15px;max-width:800px}.method strong{color:var(--cream)}.pipeline{display:flex;gap:9px;flex-wrap:wrap;font-family:Consolas,monospace;font-size:10px;letter-spacing:0;color:#c7d0bf;margin-top:24px}.pipeline span{border:1px solid var(--line);border-radius:100px;padding:9px 13px}footer{max-width:1540px;margin:auto;padding:24px 5vw;border-top:1px solid var(--line);display:flex;gap:20px;justify-content:space-between;font-size:10px;color:var(--muted)}footer a{text-decoration:underline;text-underline-offset:3px}@media(min-width:1450px){.description{font-size:13px}}@media(max-width:1000px){.grid{grid-template-columns:1fr;max-width:680px;margin:auto}.take-heading{padding:18px 24px}.take-body{padding:25px}.description{min-height:0}.cue{min-height:0}.hero-bottom{align-items:flex-start}.take-tag{font-size:11px}.downloads a{font-size:12px}.facts span{font-size:10px}.method{grid-template-columns:1fr}.method h2{max-width:none}.title-row h2{font-size:35px}}@media(max-width:600px){header{padding:20px 6vw}nav>a:first-child{display:none}.hero{padding:48px 6vw 40px}h1{font-size:clamp(48px,13vw,80px)}.hero-bottom{display:block}.stamp{margin-top:25px;display:inline-block}.intro{font-size:13px}.collection{padding:0 5vw 40px}.section-label span:last-child{display:none}.method{padding:15px 6vw 35px;gap:20px}footer{flex-direction:column;gap:10px;padding:23px 6vw}}
"""


def page(takes: list[dict]) -> str:
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="BOP! — Voice Motion Lab의 오리지널 합성 사운드 모션 아이덴티티 제작 테스트. 실제 앱에서 내보낸 SVG, WebM, 편집용 프로젝트."><title>BOP! — Sound in. Motion out. / Voice Motion Lab</title><style>{CSS}</style></head><body>
<header><a class="wordmark" href="../" aria-label="Voice Motion Lab Studio">BOP<span>!</span></a><nav><a href="#collection">PRODUCTION TAKES</a><a class="studio" href="../">Studio 열기 ↗</a></nav></header>
<main><section class="hero"><div class="eyebrow"><i aria-hidden="true"></i>VOICE MOTION LAB / ORIGINAL MOTION IDENTITY</div><h1>SOUND IN.<br><span>MOTION OUT.</span></h1><div class="hero-bottom"><p class="intro">작은 소리에도 몸이 먼저 반응하는 브랜드, <strong>BOP!</strong><br>정지 벡터 세 장에 소리로 리듬을 입혔습니다.<br>아래는 실제 앱에서 분석하고 내보낸 제작 테스트 결과입니다.</p><div class="stamp"><strong>03 TAKES. ONE IDENTITY.</strong><br>ORIGINAL SVG × SYNTHETIC SOUND<br>OPEN MODEL / LOCAL CPU</div></div></section>
<section class="collection" id="collection" aria-label="BOP! production takes"><div class="section-label"><span>THE PRODUCTION TEST / 01—03</span><span>재생을 누르면 합성 원음이 함께 나옵니다</span></div><div class="grid">{''.join(card(take) for take in takes)}</div></section>
<section class="method"><h2>Made with sound.<br>Still yours to edit.</h2><div><p><strong>실제 사람의 녹음 대신, 이번 테스트를 위해 직접 합성한 허밍·플롭 사운드를 사용했습니다.</strong> 캐릭터와 로고도 이번 프로젝트의 오리지널 SVG입니다. 외부 음원·캐릭터·생성형 영상 서비스 없이 제작했습니다.</p><p>앱은 타이밍·음높이·어택을 분석해 선택한 경로의 진행과 표정을 만듭니다. 같은 모션 계산으로 미리보기, 애니메이션 SVG와 원음 포함 WebM을 내보냅니다. 편집용 JSON을 Studio에서 다시 열면 벡터와 소리, 모션 설정을 이어서 수정할 수 있습니다.</p><p>이 페이지는 <strong>합성 입력을 사용한 프로덕션 테스트 갤러리</strong>입니다. 실제 마이크 녹음의 사용성과 다양한 사람 목소리에 대한 검증은 별도 단계입니다.</p><div class="pipeline" aria-label="테스트 구성"><span>SVG + WAV</span><span>COMFYUI</span><span>BASIC PITCH / CPU</span><span>EDITABLE MOTION</span></div></div></section></main>
<footer><span>BOP! / Voice Motion Lab · Original identity &amp; synthetic audio · MIT</span><span><a href="manifest.json">제작 파일 manifest</a> &nbsp; / &nbsp; <a href="../">Studio 열기 ↗</a></span></footer></body></html>"""


def build() -> dict:
    takes = validate_sources()
    manifest = {"format": "voice-motion-production-gallery", "version": 1,
                "title": "BOP! / Sound in. Motion out.",
                "disclosure": "Original BOP! vector identity and synthetic humming/plop audio. Not a human voice recording.",
                "license": "MIT", "native_copies": "All linked assets are copied byte-for-byte; no rerendering or retouching.",
                "test_configuration": {"flow": ["Voice Motion Lab audio import", "isolated project ComfyUI API",
                    "VoiceMotionAnalyze", "Basic Pitch ONNX / CPUExecutionProvider", "shared browser motion renderer", "native app exports"],
                    "engine_verified_from_project": True,
                    "transport_note": "Project JSON proves the analysis engine; the ComfyUI transport is the documented test configuration, not inferred from JSON."},
                "takes": []}
    for take in takes:
        slug = take["preset"]
        files = {}
        for suffix in ("webm", "svg", "json"):
            files[suffix] = copy_native(OUTPUT / f"{slug}.{suffix}", f"takes/{slug}.{suffix}")
        files["source_svg"] = copy_native(INPUT / "assets" / take["asset"], f"assets/{take['asset']}")
        files["source_audio"] = copy_native(INPUT / "audio" / take["audio"], f"audio/{take['audio']}")
        poster = OUTPUT / f"{slug}-poster.png"
        if poster.is_file():
            files["poster"] = copy_native(poster, f"takes/{slug}-poster.png")
        manifest["takes"].append({"preset": slug, "title": take["title"], "duration": take["duration"],
            "engine": take["engine"], "sample_rate": take["sample_rate"],
            "expected_impacts": take["impacts"], "actual_impacts": take["actual_impacts"],
            "synthetic_audio": True, "original_identity": True, "cue": take["cue"], "files": files})
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DESTINATION / "index.html").write_text(page(takes), encoding="utf-8")
    return {"gallery": "web/production/index.html", "takes": len(takes), "files": sum(len(take["files"]) for take in manifest["takes"])}


def main() -> int:
    try:
        print(json.dumps(build(), ensure_ascii=False))
    except (GalleryError, OSError, wave.Error) as exc:
        print(f"Production gallery not built: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
