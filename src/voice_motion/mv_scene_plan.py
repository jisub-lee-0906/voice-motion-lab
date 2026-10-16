"""Executable directing data, not a renderer or a claim of visual quality.

This module validates a finite scene plan and samples actor/prop/camera state.
It imports no model runtime and creates no image/video artifact. Art-study plans
may name explicitly pending assets; rendering must use require_render_ready=True.
"""
from __future__ import annotations

import hashlib
import copy
import json
import math
import re
from pathlib import Path,PurePosixPath,PureWindowsPath

PROJECT_ROOT=Path(__file__).resolve().parents[2]
STAGES={"art_action_study","automatic_evaluation"}
ACTIONS={"reach_and_extract","offer","open_hand","follow_arc_and_unfold","trace_door","open_door","look_at","camera_passage"}
EVENTS={"attach","detach","set_state","set_actor_state"}
FRACTION_CHANNELS={"hand_open_fraction","fold","open_fraction","visible_fraction","trace_progress","opacity"}


class ScenePlanError(ValueError):
    pass


def _require(condition,message):
    if not condition: raise ScenePlanError(message)


def _integer(value):
    return isinstance(value,int) and not isinstance(value,bool)


def _finite(value):
    return isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value)


def _relative_path(value):
    _require(isinstance(value,str) and bool(value),"A nonempty relative asset path is required")
    win=PureWindowsPath(value); posix=PurePosixPath(value.replace("\\","/"))
    _require(not win.drive and not win.is_absolute() and not posix.is_absolute() and ":" not in value and "\x00" not in value,
             "Asset paths must be relative and contain no URL or drive")
    _require(".." not in posix.parts and "."!=str(posix),"Asset path traversal is forbidden")
    return posix


def _no_host_paths(value):
    if isinstance(value,dict):
        for key,child in value.items():
            _require(key not in {"samples","raw_audio","audio_samples"},"Scene JSON must not embed original audio samples")
            _no_host_paths(child)
    elif isinstance(value,list):
        for child in value: _no_host_paths(child)
    elif isinstance(value,str):
        _require(not re.match(r"^(?:[A-Za-z]:[/\\]|[/\\]{1,2}(?!/))",value),"Scene JSON must not contain absolute host paths")


def _track(track,frames,dimension=None):
    _require(isinstance(track,dict),"Track must be an object")
    _require(track.get("interpolation") in {"hold","linear","smoothstep","hermite"},"Unsupported track interpolation")
    keys=track.get("keys")
    _require(isinstance(keys,list) and bool(keys),"Track needs keyframes")
    previous=-1; shape=None
    for key in keys:
        _require(isinstance(key,dict),"Track keyframes must be objects")
        frame=key.get("frame")
        _require(_integer(frame) and 0<=frame<=frames and frame>previous,"Track keyframes must be increasing integer frames")
        previous=frame; value=key.get("value")
        values=value if isinstance(value,list) else [value]
        _require(bool(values) and all(_finite(v) for v in values),"Track values must be finite numbers")
        if shape is None: shape=(len(values),isinstance(value,list))
        _require((len(values),isinstance(value,list))==shape,"Track dimensions and scalar/vector type must stay constant")
        if dimension is not None: _require(len(values)==dimension,"Track dimension mismatch")
        tangent=key.get("tangent")
        if tangent is not None:
            tangents=tangent if isinstance(tangent,list) else [tangent]
            _require(len(tangents)==len(values) and all(_finite(v) for v in tangents),"Invalid Hermite tangent")


def _value(track,frame):
    keys=track["keys"]
    if frame<=keys[0]["frame"]: return copy.deepcopy(keys[0]["value"])
    if frame>=keys[-1]["frame"]: return copy.deepcopy(keys[-1]["value"])
    index=next(i for i in range(len(keys)-1) if keys[i]["frame"]<=frame<keys[i+1]["frame"])
    a,b=keys[index:index+2]; span=b["frame"]-a["frame"]; u=(frame-a["frame"])/span
    vector=isinstance(a["value"],list)
    av=a["value"] if vector else [a["value"]]; bv=b["value"] if vector else [b["value"]]
    mode=track["interpolation"]
    if mode=="hold": values=av
    elif mode=="linear": values=[x+(y-x)*u for x,y in zip(av,bv)]
    elif mode=="smoothstep":
        eased=u*u*(3-2*u); values=[x+(y-x)*eased for x,y in zip(av,bv)]
    else:
        def tangent(i):
            key=keys[i]
            if "tangent" in key:
                return key["tangent"] if isinstance(key["tangent"],list) else [key["tangent"]]
            if i in (0,len(keys)-1): return [0.]*len(av)
            left,right=keys[i-1],keys[i+1]
            lv=left["value"] if vector else [left["value"]]; rv=right["value"] if vector else [right["value"]]
            return [(y-x)/(right["frame"]-left["frame"]) for x,y in zip(lv,rv)]
        ma,mb=tangent(index),tangent(index+1)
        values=[(2*u**3-3*u*u+1)*x+(u**3-2*u*u+u)*span*dx+(-2*u**3+3*u*u)*y+(u**3-u*u)*span*dy
                for x,y,dx,dy in zip(av,bv,ma,mb)]
    return list(values) if vector else values[0]


def _bounded_fraction(track,frames,label):
    # Validate both keys and integer-frame interpolation: valid endpoints alone
    # do not prevent an overshooting Hermite tangent from producing fraction 2.
    for key in track["keys"]:
        _require(_finite(key["value"]) and 0<=key["value"]<=1,f"{label} must be a finite scalar in [0,1]")
    for frame in range(frames):
        value=_value(track,frame)
        _require(_finite(value) and -1e-9<=value<=1+1e-9,f"{label} interpolation must remain in [0,1]")


def _channel_value(name,track,frame):
    value=_value(track,frame)
    # Only remove floating-point round-off after the full bounded-track check.
    return max(0.,min(1.,value)) if name in FRACTION_CHANNELS else value


def _camera_state(scene,definition,frame):
    center=_value(definition["center"],frame); zoom=_value(definition["zoom"],frame)
    if isinstance(zoom,list): zoom=zoom[0]
    _require(all(_finite(v) for v in center) and _finite(zoom) and zoom>0,"Sampled camera transform must be finite with positive zoom")
    result={"center":center,"zoom":zoom,"passage_complete":False}
    portal=definition.get("portal_prop")
    if portal:
        prop=next(p for p in scene["props"] if p["id"]==portal)
        bounds=prop["inner_bounds"]; width,height=scene["canvas"]["width"],scene["canvas"]["height"]
        screen=[width/2+(bounds[0]-center[0])*zoom,height/2+(bounds[1]-center[1])*zoom,
                width/2+(bounds[2]-center[0])*zoom,height/2+(bounds[3]-center[1])*zoom]
        _require(all(_finite(value) for value in screen),"Portal projection must stay finite")
        result["portal_screen_bounds"]=screen
        opening=_channel_value("open_fraction",prop["tracks"]["open_fraction"],frame)
        result["passage_complete"]=bool(opening==1 and screen[0]<=0 and screen[1]<=0 and screen[2]>=width and screen[3]>=height)
    return result


def _actor(scene,actor_id,frame):
    actor=next(a for a in scene["actors"] if a["id"]==actor_id)
    root=_value(actor["root_track"],frame)
    anchors={name:[root[0]+point[0],root[1]+point[1]] for name,point in actor["anchors"].items()}
    for name,track in actor.get("anchor_tracks",{}).items():
        local=_value(track,frame); anchors[name]=[root[0]+local[0],root[1]+local[1]]
    arm=actor.get("right_arm")
    joints=None
    if arm:
        shoulder=anchors[arm["shoulder_anchor"]]; grip=anchors[arm["grip_anchor"]]
        dx,dy=grip[0]-shoulder[0],grip[1]-shoulder[1]; distance=math.hypot(dx,dy)
        a,b=arm["upper_length"],arm["fore_length"]
        _require(abs(a-b)+1e-6<=distance<=a+b-1e-6,"Right-hand path exceeds arm reach")
        along=(a*a-b*b+distance*distance)/(2*distance)
        height=math.sqrt(max(0,a*a-along*along))*arm.get("bend",1)
        elbow=[shoulder[0]+along*dx/distance-height*dy/distance,
               shoulder[1]+along*dy/distance+height*dx/distance]
        joints={"shoulder":shoulder,"elbow":elbow,"grip":grip}
    return {"root":root,"anchors":anchors,"right_arm":joints,
            **{name:_channel_value(name,track,frame) for name,track in actor.get("tracks",{}).items()}}


def _state(scene,frame):
    states={p["id"]:p["initial_state"] for p in scene["props"]}; attachments={}
    actor_states={a["id"]:copy.deepcopy(a.get("initial_state",{})) for a in scene["actors"]}
    for event in scene["events"]:
        if event["frame"]>frame: break
        kind=event["kind"]
        if kind=="set_actor_state": actor_states[event["actor"]].update(event["changes"]); continue
        prop=event["prop"]
        if kind=="attach": attachments[prop]={"actor":event["actor"],"anchor":event["anchor"]}
        elif kind=="detach": attachments.pop(prop,None)
        states[prop]=event["state"]
    return states,attachments,actor_states


def _prop_position(scene,prop,frame,attachments):
    attachment=attachments.get(prop["id"])
    if attachment:
        return _actor(scene,attachment["actor"],frame)["anchors"][attachment["anchor"]]
    return _value(prop["position_track"],frame)


def _file_status(record,asset_root,strict):
    relative=_relative_path(record["relative_asset"])
    if asset_root is None: return False
    target=(asset_root/str(relative)).resolve()
    _require(target.is_relative_to(asset_root),"Resolved asset escapes its project root")
    exists=target.is_file() and not target.is_symlink()
    if strict:
        _require(exists,f"Required asset is missing: {record['relative_asset']}")
        expected=record.get("sha256")
        _require(isinstance(expected,str) and re.fullmatch(r"[0-9a-f]{64}",expected),"A real asset SHA256 is required for rendering")
        digest=hashlib.sha256()
        with target.open("rb") as source:
            for chunk in iter(lambda:source.read(1024*1024),b""): digest.update(chunk)
        _require(digest.hexdigest()==expected,f"Asset hash mismatch: {record['relative_asset']}")
    return exists


def validate_scene_plan(scene:dict,*,asset_root:Path|None=None,require_render_ready=False) -> dict:
    """Raise ScenePlanError on invalid data; pending art never becomes render-ready."""
    _require(isinstance(scene,dict),"Scene plan must be an object"); _no_host_paths(scene)
    _require(scene.get("format")=="voice-motion-directed-scene" and scene.get("version")==1,"Unsupported scene format")
    stage=scene.get("stage"); _require(stage in STAGES,"Unknown production stage")
    canvas=scene.get("canvas",{}); _require(isinstance(canvas,dict),"Canvas must be an object"); total=canvas.get("frames")
    _require(_integer(total) and 1<=total<=1440,"Invalid total frame count")
    _require(all(_integer(canvas.get(k)) and canvas[k]>0 for k in ("width","height","fps")),"Canvas dimensions and FPS must be positive integers")
    _require(canvas["width"]<=3840 and canvas["height"]<=2160 and canvas["fps"]<=60,"Scene canvas exceeds bounded proof limits")
    if asset_root is not None:
        asset_root=Path(asset_root).resolve()
        _require(asset_root.is_relative_to(PROJECT_ROOT.resolve()) and asset_root.is_dir(),"Asset root must be an existing project directory")
    if require_render_ready: _require(asset_root is not None,"Rendering requires an explicit asset root")
    record=scene.get("production_record",{}); _require(isinstance(record,dict),"Production record must be an object")
    selection=record.get("selected_by",{}); _require(isinstance(selection,dict),"Selection provenance must be an object")
    _require(selection.get("kind") in {"human","automatic"} and bool(selection.get("id")),"Selection provenance is required")
    _require(isinstance(record.get("manual_edits"),list) and isinstance(record.get("candidate_budget"),dict),"Production budget and manual-edit log are required")
    _require(bool(record["candidate_budget"]) and all(_integer(v) and 1<=v<=64 for v in record["candidate_budget"].values()),"Candidate budgets must be finite positive integers up to 64")
    if stage=="automatic_evaluation":
        _require(selection["kind"]=="automatic" and bool(selection.get("version")),"Automatic evaluation cannot use human-selected candidates")
        _require(not record["manual_edits"],"Automatic evaluation cannot hide manual edits")
        _require(isinstance(record.get("candidate_log"),list) and bool(record["candidate_log"]),"Automatic evaluation requires its candidate-selection log")
    audio=scene.get("audio",{}); _require(isinstance(audio,dict),"Audio metadata must be an object"); _relative_path(audio.get("relative_asset"))
    _require(bool(audio.get("rights")),"Audio rights evidence/source is required")
    if require_render_ready:
        _require(audio.get("rights_status") in {"owned","licensed","public_domain"},"Pending audio rights cannot be treated as a ready input")
    audio_present=_file_status(audio,asset_root,require_render_ready)
    _require(isinstance(scene.get("lyrics"),dict) and bool(scene["lyrics"].get("source")) and bool(scene["lyrics"].get("timing_source")),"Lyric text/timing provenance is required")
    if stage=="automatic_evaluation":
        _require(scene["lyrics"]["source"] not in {"fixture_not_asr","provided_lyrics"},"Audio-only evaluation cannot substitute fixture/provided lyrics")
        _require(scene["lyrics"]["timing_source"]!="planning_placeholder","Automatic evaluation cannot claim placeholder timing as analysis")
    assets=scene.get("assets"); actors=scene.get("actors"); props=scene.get("props"); shots=scene.get("shots"); events=scene.get("events")
    _require(all(isinstance(items,list) and bool(items) for items in (assets,actors,props,shots,events)),"Scene needs assets, actors, props, shots, and events")
    _require(all(isinstance(item,dict) for items in (assets,actors,props,shots,events) for item in items),"Scene records must be objects")
    def ids(items,label):
        names=[item.get("id") for item in items]
        _require(all(isinstance(i,str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*",i) for i in names) and len(set(names))==len(names),f"Invalid or duplicate {label} IDs")
        return set(names)
    asset_ids=ids(assets,"asset"); actor_ids=ids(actors,"actor"); prop_ids=ids(props,"prop"); ids(shots,"shot"); ids(events,"event")
    all_art_ready=True; pending=[]
    for asset in assets:
        _require(asset.get("kind") in {"rgba_image","vector","rig","pose_sequence","video_layer","mask"},"Unknown asset representation")
        provenance=asset.get("provenance",{}); _require(isinstance(provenance,dict),"Asset provenance must be an object"); kind=provenance.get("kind")
        _require(kind in {"authored","model_generated","planned_model_generated","planned_authored"},"Every asset needs honest origin provenance")
        review=asset.get("review",{}); _require(isinstance(review,dict) and review.get("status") in {"pending","approved","rejected"},"Asset review status is required")
        if asset.get("sha256") is not None: _require(bool(re.fullmatch(r"[0-9a-f]{64}",str(asset["sha256"]))),"Invalid asset hash format")
        exists=_file_status(asset,asset_root,require_render_ready)
        ready=exists and kind in {"authored","model_generated"} and review["status"]=="approved"
        if not ready: pending.append(asset["id"]); all_art_ready=False
        if require_render_ready:
            _require(kind not in {"planned_model_generated","planned_authored"},"Planned artwork is not an actual render asset")
            _require(review["status"]=="approved" and bool(review.get("by")),"Unreviewed/rejected artwork cannot be rendered")
            _require(bool(provenance.get("license")),"Asset license is required before rendering")
            if kind=="model_generated":
                _require(all(provenance.get(k) is not None and provenance.get(k)!="" for k in ("model_id","revision","prompt","seed","candidate_id")),"Generated-asset provenance is incomplete")
            else: _require(bool(provenance.get("creator")),"Authored asset needs its creator")
        if stage=="automatic_evaluation" and kind=="model_generated":
            choices=[c for c in record["candidate_log"] if isinstance(c,dict) and c.get("asset_id")==asset["id"] and c.get("candidate_id")==provenance.get("candidate_id")]
            _require(len(choices)==1 and choices[0].get("selected") is True and choices[0].get("selection_kind")=="automatic","Generated assets need an automatic candidate choice in the run log")
    for actor in actors:
        _require(isinstance(actor.get("asset_ids"),list) and bool(actor["asset_ids"]) and set(actor["asset_ids"])<=asset_ids,"Actor references an unknown asset")
        _track(actor.get("root_track"),total,2)
        _require(isinstance(actor.get("initial_state"),dict),"Actor initial state must be explicit")
        _require(isinstance(actor.get("anchors"),dict) and bool(actor["anchors"]),"Actor anchors are required")
        for name,value in actor["anchors"].items():
            _require(isinstance(value,list) and len(value)==2 and all(_finite(v) for v in value),"Invalid actor anchor")
        for name,track in actor.get("anchor_tracks",{}).items(): _track(track,total,2)
        for name,track in actor.get("tracks",{}).items():
            _track(track,total)
            if name in FRACTION_CHANNELS: _bounded_fraction(track,total,name)
        if actor.get("look_target"):
            _require(actor["look_target"] in prop_ids and actor.get("eye_anchor") in actor["anchors"],"Gaze target/eye anchor must exist")
        arm=actor.get("right_arm")
        if arm:
            names=set(actor["anchors"])|set(actor.get("anchor_tracks",{}))
            _require(arm.get("shoulder_anchor") in names and arm.get("grip_anchor") in names,"IK references an unknown anchor")
            _require(all(_finite(arm.get(k)) and arm[k]>0 for k in ("upper_length","fore_length")) and arm.get("bend",1) in {-1,1},"Invalid arm lengths/bend")
    for prop in props:
        _require(prop.get("asset_id") in asset_ids,"Prop references an unknown asset")
        _require(isinstance(prop.get("states"),list) and all(isinstance(s,str) and bool(s) for s in prop["states"]) and len(set(prop["states"]))==len(prop["states"]) and prop.get("initial_state") in prop["states"],"Prop states must be unique and include the initial state")
        _require(isinstance(prop.get("transitions"),list) and all(isinstance(pair,list) and len(pair)==2 and all(s in prop["states"] for s in pair) for pair in prop["transitions"]),"Prop needs explicit allowed state transitions")
        _track(prop.get("position_track"),total,2)
        for name,track in prop.get("tracks",{}).items():
            _track(track,total)
            if name in FRACTION_CHANNELS: _bounded_fraction(track,total,name)
        if "trace" in prop:
            trace=prop["trace"]
            _require(trace.get("origin_prop") in prop_ids and trace["origin_prop"]!=prop["id"],"Trace needs a distinct source prop")
            _require(isinstance(trace.get("points"),list) and len(trace["points"])>=2 and all(isinstance(p,list) and len(p)==2 and all(_finite(x) for x in p) for p in trace["points"]),"Invalid trace path")
            _require("trace_progress" in prop.get("tracks",{}),"Tracing prop needs progress")
            _require(_integer(trace.get("dock_frame")) and 0<=trace["dock_frame"]<total,"Trace needs a source docking frame")
        if "inner_bounds" in prop:
            bounds=prop["inner_bounds"]
            _require(isinstance(bounds,list) and len(bounds)==4 and all(_finite(v) for v in bounds) and bounds[0]<bounds[2] and bounds[1]<bounds[3],"Invalid portal bounds")
    cursor=0
    for shot in shots:
        span=shot.get("range",[])
        _require(isinstance(span,list) and len(span)==2 and all(_integer(i) for i in span) and span[0]==cursor and span[1]>span[0] and span[1]<=total,"Shots must cover every frame exactly once in order")
        cursor=span[1]
        camera=shot.get("camera",{})
        _track(camera.get("center"),total,2); _track(camera.get("zoom"),total,1)
        _require(all((key["value"] if not isinstance(key["value"],list) else key["value"][0])>0 for key in camera["zoom"]["keys"]),"Camera zoom must be positive")
        if camera.get("portal_prop"):
            _require(camera["portal_prop"] in prop_ids,"Camera references an unknown portal")
            portal=next(p for p in props if p["id"]==camera["portal_prop"])
            _require("inner_bounds" in portal and "open_fraction" in portal.get("tracks",{}),"Camera portal needs bounds and an opening track")
        for frame in range(span[0],span[1]): _camera_state(scene,camera,frame)
        _require(isinstance(shot.get("actions"),list) and bool(shot["actions"]),"Every shot needs actual directed actions")
        for action in shot["actions"]:
            _require(action.get("kind") in ACTIONS,"Unknown action cannot silently become sway")
            arange=action.get("range",[])
            _require(len(arange)==2 and all(_integer(f) for f in arange) and span[0]<=arange[0]<arange[1]<=span[1],"Action range must stay inside its shot")
            if "actor" in action: _require(action["actor"] in actor_ids,"Unknown action actor")
            if "prop" in action: _require(action["prop"] in prop_ids,"Unknown action prop")
            _require(isinstance(action.get("channels"),list) and bool(action["channels"]),"Action must name actual sampled channels")
            for channel in action["channels"]:
                _require(isinstance(channel,str),"Action channel must be a path")
                parts=channel.split(".")
                if parts[0]=="actors":
                    _require(len(parts)>=3 and parts[1] in actor_ids,"Action channel references unknown actor")
                    definition=next(a for a in actors if a["id"]==parts[1])
                    legal=(len(parts)==3 and parts[2] in {"root","right_arm","gaze_degrees"}|set(definition.get("tracks",{})))
                    legal=legal or (len(parts)==4 and parts[2]=="anchors" and parts[3] in set(definition["anchors"])|set(definition.get("anchor_tracks",{})))
                    legal=legal or (len(parts)==4 and parts[2]=="state" and parts[3] in definition.get("initial_state",{}))
                elif parts[0]=="props":
                    _require(len(parts)==3 and parts[1] in prop_ids,"Action channel references unknown prop")
                    definition=next(p for p in props if p["id"]==parts[1])
                    legal=parts[2] in {"position","state","attachment"}|set(definition.get("tracks",{}))
                else: legal=len(parts)==2 and parts[0]=="camera" and parts[1] in {"center","zoom"}
                _require(legal,"Action names a channel the sampler does not implement")
        _require(isinstance(shot.get("keyposes"),list) and bool(shot["keyposes"]),"Shot needs keyposes")
        for pose in shot["keyposes"]:
            _require(_integer(pose.get("frame")) and span[0]<=pose["frame"]<span[1],"Keypose outside shot")
            _require(pose.get("actor") in actor_ids and pose.get("asset_id") in asset_ids,"Keypose references unknown actor/asset")
        _require(isinstance(shot.get("typography"),dict),"Typography plan is required")
    _require(cursor==total,"Shot coverage is incomplete")
    action_records=[action for shot in shots for action in shot["actions"]]
    ids(action_records,"action")
    # Validate causal state transitions in event order. Attachment is a geometric
    # constraint; a future renderer cannot freely move the star before release.
    states={p["id"]:p["initial_state"] for p in props}; attached={}; previous=-1
    for event in events:
        frame=event.get("frame"); kind=event.get("kind")
        _require(_integer(frame) and 0<=frame<total and frame>=previous,"Events must be sorted integer frames")
        previous=frame; _require(kind in EVENTS,"Unknown state event")
        if kind=="set_actor_state":
            _require(event.get("actor") in actor_ids and isinstance(event.get("changes"),dict),"Invalid actor state event")
            actor=next(a for a in actors if a["id"]==event["actor"])
            _require(set(event["changes"])<=set(actor["initial_state"]),"Actor event modifies an undeclared state channel")
            continue
        prop_id=event.get("prop"); _require(prop_id in prop_ids,"Unknown event prop")
        prop=next(p for p in props if p["id"]==prop_id)
        _require(event.get("require_state")==states[prop_id],"Event precondition does not match the preceding state")
        _require(event.get("state") in prop["states"],"Unknown resulting prop state")
        _require([states[prop_id],event["state"]] in prop["transitions"],"Prop state transition is not allowed")
        if kind=="attach":
            _require(prop_id not in attached and event.get("actor") in actor_ids,"Invalid duplicate attachment")
            actor=next(a for a in actors if a["id"]==event["actor"])
            _require(event.get("anchor") in set(actor["anchors"])|set(actor.get("anchor_tracks",{})),"Unknown attachment anchor")
            before=_value(prop["position_track"],frame)
            after=_actor(scene,event["actor"],frame)["anchors"][event["anchor"]]
            _require(math.dist(before,after)<=.001,"Attachment would teleport the prop")
            attached[prop_id]={"actor":event["actor"],"anchor":event["anchor"]}
        elif kind=="detach":
            _require(prop_id in attached,"Cannot release a prop that is not attached")
            attachment=attached[prop_id]
            held=_actor(scene,attachment["actor"],frame)["anchors"][attachment["anchor"]]
            free=_value(prop["position_track"],frame)
            _require(math.dist(held,free)<=.001,"Detach position must be continuous")
            epsilon=.001
            left=_actor(scene,attachment["actor"],frame-epsilon)["anchors"][attachment["anchor"]]
            right=_value(prop["position_track"],frame+epsilon)
            _require(math.dist([(h-l)/epsilon for h,l in zip(held,left)],[(r-f)/epsilon for r,f in zip(right,free)])<=.01,"Detach velocity must be continuous")
            attached.pop(prop_id)
        states[prop_id]=event["state"]
    # Every sampled key/between-key instant remains reachable by the fixed arm.
    for actor in actors:
        for frame in range(total): _actor(scene,actor["id"],frame)
    for prop in props:
        if "trace" not in prop: continue
        trace=prop["trace"]; dock=trace["dock_frame"]
        states,attachments,_=_state(scene,dock)
        origin=next(p for p in props if p["id"]==trace["origin_prop"])
        _require(origin["id"] not in attachments and states[origin["id"]]==trace.get("require_source_state"),"Door trace requires the released source prop in its docking state")
        _require(math.dist(_prop_position(scene,origin,dock,attachments),trace["points"][0])<=.001,"Door trace must start at the same star")
        previous=0.
        for frame in range(total):
            amount=_value(prop["tracks"]["trace_progress"],frame)
            _require(_finite(amount) and previous-1e-9<=amount<=1,"Door trace progress must advance from 0 to 1")
            if frame<dock: _require(amount==0,"Door cannot trace before the source prop arrives")
            previous=amount
    # Action names must describe the channels and causal events that really
    # exist. A decorative action label cannot substitute for its state change.
    for action in action_records:
        kind=action["kind"]; start,end=action["range"]; channels=set(action["channels"])
        actor_id=action.get("actor"); prop_id=action.get("prop")
        prop=next((p for p in props if p["id"]==prop_id),None)
        if kind in {"reach_and_extract","offer","open_hand","look_at"}:
            _require(actor_id in actor_ids and prop_id in prop_ids,"Actor action needs an actor and a prop")
        if kind=="reach_and_extract":
            _require(any(e["kind"]=="attach" and e.get("prop")==prop_id and e.get("actor")==actor_id and start<=e["frame"]<end for e in events),"Extraction needs a real attachment event")
            _require(f"actors.{actor_id}.anchors.right_grip" in channels and f"props.{prop_id}.attachment" in channels,"Extraction needs actual hand and prop-attachment channels")
        elif kind=="offer":
            for boundary in (start,end-1):
                _,active,_=_state(scene,boundary)
                _require(active.get(prop_id,{}).get("actor")==actor_id,"Offer must keep the same prop attached")
        elif kind=="open_hand":
            actor=next(a for a in actors if a["id"]==actor_id)
            hand=actor.get("tracks",{}).get("hand_open_fraction")
            _require(hand is not None and f"actors.{actor_id}.hand_open_fraction" in channels,"Opening a hand needs its numeric shape channel")
            _require(_value(hand,start)==0 and _value(hand,end-1)==1,"Hand action must actually open from closed to open")
            _require(any(e["kind"]=="detach" and e.get("prop")==prop_id and e["frame"]==end for e in events),"Hand opening must connect to its explicit release")
        elif kind=="look_at":
            actor=next(a for a in actors if a["id"]==actor_id)
            _require(actor.get("look_target") in prop_ids and actor.get("eye_anchor") in actor["anchors"],"Look-at actions require a valid look_target and eye_anchor")
        elif kind=="follow_arc_and_unfold":
            _require(prop is not None and "fold" in prop.get("tracks",{}),"Flight/unfold needs a prop and fold channel")
            _require({f"props.{prop_id}.position",f"props.{prop_id}.fold"}<=channels,"Flight/unfold must sample position and fold")
            _require(any(e["kind"]=="detach" and e.get("prop")==prop_id and e["frame"]==start for e in events),"Independent flight must begin at the release event")
            _require(math.dist(_value(prop["position_track"],start),_value(prop["position_track"],end-1))>1 and _value(prop["tracks"]["fold"],end-1)>_value(prop["tracks"]["fold"],start),"Flight/unfold must change both position and fold")
        elif kind=="trace_door":
            _require(prop is not None and "trace" in prop and f"props.{prop_id}.trace_progress" in channels,"Door action needs its source-linked trace channel")
        elif kind=="open_door":
            _require(prop is not None and "trace_progress" in prop.get("tracks",{}) and "open_fraction" in prop["tracks"],"Door opening needs trace and opening tracks")
            _require(_value(prop["tracks"]["trace_progress"],start)==1 and _value(prop["tracks"]["open_fraction"],start)==0 and _value(prop["tracks"]["open_fraction"],end)==1,"Door must finish tracing before opening")
        elif kind=="camera_passage":
            _require({"camera.center","camera.zoom"}<=channels and prop is not None and "inner_bounds" in prop,"Passage needs real camera movement and portal bounds")
            _require(_value(prop["tracks"]["open_fraction"],start)==1,"Camera cannot pass through a closed door")
            shot=next(shot for shot in shots if action in shot["actions"])
            camera=shot["camera"]
            _require(camera.get("portal_prop")==prop_id,"Camera passage must target its actual portal")
            initial=_camera_state(scene,camera,start)
            final=_camera_state(scene,camera,min(end,total-1))
            moved=math.dist(initial["center"],final["center"])>1e-6 or abs(initial["zoom"]-final["zoom"])>1e-6
            _require(moved,"Camera passage must actually move the camera")
            _require(not initial["passage_complete"] and final["passage_complete"],"Camera passage must enter the open door interior in its final projection")
    ready=bool(require_render_ready and audio_present and all_art_ready)
    return {"valid":True,"render_ready":ready,"stage":stage,"frames":total,"pending_assets":pending,
            "quality_status":"not_assessed","artifact_success":False,
            "warnings":([] if ready else ["Planning/sampling only: no image or video success; missing or pending assets cannot render."])}


def _polyline(points,progress):
    lengths=[math.dist(a,b) for a,b in zip(points,points[1:])]; remaining=max(0,min(1,progress))*sum(lengths)
    for start,end,length in zip(points,points[1:],lengths):
        if remaining<=length:
            u=remaining/length if length else 0.; return [a+(b-a)*u for a,b in zip(start,end)]
        remaining-=length
    return points[-1]


def sample_scene(scene:dict,frame:int) -> dict:
    """Return transforms/state only. It does not render or approve any artwork."""
    validation=validate_scene_plan(scene)
    _require(_integer(frame) and 0<=frame<scene["canvas"]["frames"],"Sample frame must be an integer inside the scene")
    states,attachments,actor_states=_state(scene,frame)
    actors={a["id"]:{**_actor(scene,a["id"],frame),"state":actor_states[a["id"]]} for a in scene["actors"]}
    props={}
    for prop in scene["props"]:
        values={name:_channel_value(name,track,frame) for name,track in prop.get("tracks",{}).items()}
        result={"world_xy":_prop_position(scene,prop,frame,attachments),"state":states[prop["id"]],"attached_to":attachments.get(prop["id"]),**values}
        if "trace" in prop:
            amount=values["trace_progress"]
            result["trace_tip"]=_polyline(prop["trace"]["points"],amount) if amount>0 else None
            result["trace_origin_prop"]=prop["trace"]["origin_prop"]
        props[prop["id"]]=result
    shot=next(s for s in scene["shots"] if s["range"][0]<=frame<s["range"][1])
    camera=_camera_state(scene,shot["camera"],frame)
    active=[a["id"] for a in shot["actions"] if a["range"][0]<=frame<a["range"][1]]
    for actor in scene["actors"]:
        look_actions=[action for shot_definition in scene["shots"] for action in shot_definition["actions"]
                      if action["kind"]=="look_at" and action.get("actor")==actor["id"] and action["range"][0]<=frame]
        target=max(look_actions,key=lambda action:action["range"][0]).get("prop") if look_actions else actor.get("look_target")
        if target:
            eye=actors[actor["id"]]["anchors"][actor["eye_anchor"]]
            position=props[target]["world_xy"]
            actors[actor["id"]]["gaze_degrees"]=math.degrees(math.atan2(position[1]-eye[1],position[0]-eye[0]))
            actors[actor["id"]]["gaze_target_prop"]=target
        poses=[pose for pose in shot["keyposes"] if pose["actor"]==actor["id"]]
        previous=[pose for pose in poses if pose["frame"]<=frame]
        following=[pose for pose in poses if pose["frame"]>frame]
        actors[actor["id"]]["keypose_asset_id"]=(previous[-1] if previous else poses[0])["asset_id"] if poses else None
        actors[actor["id"]]["next_keypose_asset_id"]=following[0]["asset_id"] if following else None
    return {"kind":"directed_scene_state_sample","rendered":False,"artifact_success":False,"quality_status":"not_assessed",
            "frame":frame,"time_seconds":frame/scene["canvas"]["fps"],"shot_id":shot["id"],"actors":actors,"props":props,
            "camera":camera,"active_actions":active,"typography":copy.deepcopy(shot["typography"]),"planning_warnings":validation["warnings"]}


def load_scene_plan(path:Path,*,asset_root:Path|None=None,require_render_ready=False) -> dict:
    path=Path(path).resolve()
    _require(path.is_file() and path.is_relative_to(PROJECT_ROOT.resolve()),"Scene plan must be an existing project file")
    _require(path.stat().st_size<=2*1024*1024,"Scene plan exceeds the 2MiB contract limit")
    try: scene=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError) as error: raise ScenePlanError("Cannot read scene JSON") from error
    validate_scene_plan(scene,asset_root=asset_root or path.parent,require_render_ready=require_render_ready)
    return scene
