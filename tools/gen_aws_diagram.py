"""Author both architecture diagrams.

    docs/architecture-aws-full.excalidraw    the deployment, with the infrastructure a
                                             reviewer would ask about.
    docs/architecture-aws-slide.excalidraw   the same deployment, fewer boxes, sized to
                                             survive projection.

THE TEST EVERY LINE OF TEXT HERE HAS TO PASS: does it say what a box IS, or does it argue
why a choice was made? The second kind comes off. The diagram says what is deployed; the
person presenting says why. An earlier version had two justification callouts, a paragraph
on the batch gate, a cost-control box and a roadmap sentence, and between them the prose
outweighed the flowchart, which reads as over-built.

Laid out in code so the geometry is reproducible and a label change does not mean nudging
boxes. Output is a normal Excalidraw file, editable at excalidraw.com.

COLOUR. One hue, three tones, two accents:

    compute   containers running our code
    managed   AWS services we configure and do not run
    store     data at rest, and the external APIs at the boundary
    batch     the second request path, so the two are separable at a glance
    accent    the not-built line, and nothing else
"""
import json, pathlib, random

DOCS = pathlib.Path(__file__).resolve().parents[1] / "docs"

INK   = "#1e1e1e"
GREY  = "#868e96"
FAINT = "#adb5bd"
NONE  = "transparent"

EDGE    = "#1864ab"
# Three legend categories, three hues. They were three tints of one blue, which is a
# lightness difference only and does not survive a projector or a greyscale print.
COMPUTE = ("#1864ab", "#a5d8ff")   # our code       blue
MANAGED = ("#2b8a3e", "#b2f2bb")   # AWS managed    green
STORE   = ("#e67700", "#ffec99")   # data, external amber
ACCENT  = ("#c92a2a", "#ffe3e3")
PLAIN   = (INK, "#f8f9fa")
BATCH   = "#5f3dc4"

els: list = []
_n = [0]


def reset():
    els.clear()
    _n[0] = 0
    random.seed(20260731)


def _base(kind, x, y, w, h, **kw):
    _n[0] += 1
    return {
        "id": f"e{_n[0]}", "type": kind,
        "x": x, "y": y, "width": w, "height": h, "angle": 0,
        "strokeColor": kw.get("stroke", INK),
        "backgroundColor": kw.get("bg", NONE),
        "fillStyle": kw.get("fill", "solid"),
        "strokeWidth": kw.get("sw", 1),
        "strokeStyle": kw.get("ss", "solid"),
        "roughness": kw.get("rough", 1),
        "opacity": kw.get("opacity", 100),
        "groupIds": [], "frameId": None,
        "roundness": kw.get("roundness", {"type": 3}),
        "seed": random.randint(1, 2**31),
        "version": 1, "versionNonce": random.randint(1, 2**31),
        "isDeleted": False, "boundElements": None, "updated": 1,
        "link": None, "locked": False,
    }


def box(x, y, w, h, label, colour=None, size=16, sw=1, ss="solid",
        roundness={"type": 3}, align="center", rough=1, text_colour=None, opacity=100):
    stroke, bg = colour if colour else (INK, NONE)
    els.append(_base("rectangle", x, y, w, h, stroke=stroke, bg=bg, sw=sw, ss=ss,
                     roundness=roundness, rough=rough, opacity=opacity))
    if label:
        text(x + w / 2, y + h / 2, label, size=size, align=align, anchor="middle",
             colour=text_colour or INK, opacity=opacity)


def text(x, y, s, size=16, align="left", anchor="topleft", colour=INK, family=5,
         width=None, opacity=100):
    lines = s.split("\n")
    lh = 1.25
    h = len(lines) * size * lh
    w = width if width else max(len(l) for l in lines) * size * 0.58
    if anchor == "middle":
        x, y = x - w / 2, y - h / 2
    elif anchor == "topcentre":
        x = x - w / 2
    e = _base("text", x, y, w, h, stroke=colour, roundness=None, opacity=opacity)
    e.update({"text": s, "fontSize": size, "fontFamily": family,
              "textAlign": align if anchor == "topleft" else "center",
              "verticalAlign": "top", "containerId": None, "originalText": s,
              "lineHeight": lh, "autoResize": True})
    els.append(e)


def arrow(x1, y1, x2, y2, colour=INK, sw=1, ss="solid", dashed=False, via=None,
          opacity=100, head="arrow"):
    pts = [[0, 0]] + [[vx - x1, vy - y1] for vx, vy in (via or [])] + [[x2 - x1, y2 - y1]]
    xs = [x1] + [v[0] for v in (via or [])] + [x2]
    ys = [y1] + [v[1] for v in (via or [])] + [y2]
    e = _base("arrow", x1, y1, max(xs) - min(xs) or 1, max(ys) - min(ys) or 1,
              stroke=colour, sw=sw, ss="dashed" if dashed else ss,
              roundness={"type": 2}, opacity=opacity)
    e.update({"points": pts, "lastCommittedPoint": None,
              "startBinding": None, "endBinding": None,
              "startArrowhead": None, "endArrowhead": head, "elbowed": False})
    els.append(e)


def legend(x, y, size=13, gap=26, swatch=16):
    """Three entries, and every one of them is on a box in the drawing.

    There used to be a fourth, ACCENT labelled "not built". Nothing was ever drawn in it:
    ACCENT was used only by the two design-note callouts, so the legend told a reader that a
    red box meant something broken and then the only red boxes were commentary. Both callouts
    are plain text now and the entry is gone.
    """
    for i, (colour, label) in enumerate([
            (COMPUTE, "our code"), (MANAGED, "AWS managed"),
            (STORE, "data + external")]):
        yy = y + i * gap
        box(x, yy, swatch, swatch, "", colour)
        text(x + swatch + 10, yy + 1, label, size=size, colour=GREY)


def write(name):
    (DOCS / f"{name}.excalidraw").write_text(json.dumps(
        {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
         "elements": list(els),
         "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
         "files": {}}, indent=2))
    print(f"{name}.excalidraw  ({len(els)} elements)")


# =======================================================================================
def build_full():
    reset()
    ROW, H = 400, 58

    text(60, 40, "BioLead: the deployment", size=36)
    legend(1660, 46)

    box(40, 140, 1900, 672, "", (GREY, NONE), ss="dashed")

    box(80, ROW - H / 2, 130, H, "Users", STORE, size=15)
    arrow(210, ROW, 246, ROW)
    box(248, ROW - H / 2, 120, H, "WAF", MANAGED, size=15)
    arrow(368, ROW, 404, ROW)
    box(406, ROW - H / 2, 174, H, "CloudFront", MANAGED, size=15)
    arrow(493, ROW + H / 2, 493, 466)
    box(406, 468, 174, 56, "S3\nReact bundle", STORE, size=14)
    arrow(580, ROW, 676, ROW)

    box(640, 292, 900, 410, "", (GREY, NONE), ss="dashed")
    text(656, 302, "VPC", size=14, colour=GREY)

    box(664, 338, 202, 130, "", (GREY, NONE), ss="dotted")
    text(676, 346, "public subnet", size=12, colour=GREY)
    box(680, ROW - H / 2, 170, H, "ALB\n/api/*", MANAGED, size=15)
    # A label on the box, not an argument about it.
    text(680, 436, "no 29 second limit", size=12, colour=GREY)

    box(886, 326, 640, 352, "", (GREY, NONE), ss="dotted")
    text(898, 334, "private subnet", size=12, colour=GREY)

    # The compute group. One arrow leaves it for the service stack, so a reader is not asked
    # to trace five grey lines to work out which service belongs to which container.
    box(898, 348, 246, 250, "", (GREY, NONE), ss="dotted")
    box(908, 362, 226, 84, "ECS Fargate\nFastAPI, 1-2 tasks", COMPUTE, size=14)
    arrow(850, ROW, 904, ROW)
    box(908, 496, 226, 58, "background task\nsame container", COMPUTE, size=13, ss="dotted")
    arrow(1021, 446, 1021, 494, colour=BATCH, sw=2)
    text(908, 562, "a list · client polls\nagent off above 25 genes", size=13, colour=BATCH)

    COL_X, COL_W = 1188, 300
    rows = [("Secrets Manager", MANAGED), ("DynamoDB\nrun state + evidence cache", STORE),
            ("CloudWatch Logs", MANAGED), ("Bedrock (VPC endpoint)", MANAGED),
            ("NAT Gateway", MANAGED)]
    ys = [348, 400, 490, 542, 616]
    for (label, colour), y in zip(rows, ys):
        h = 54 if "\n" in label else 38
        box(COL_X, y, COL_W, h, label, colour, size=13)
    text(COL_X, 458, "keyed on data version", size=12, colour=GREY)
    text(COL_X, 584, "VPC endpoint, IAM not a key", size=12, colour=GREY)
    box(1176, 336, 324, 332, "", (GREY, NONE), ss="dotted")
    # ONE line from the compute group to the column, landing on the column and not on
    # whichever service happened to sit at that height.
    arrow(1148, 470, 1170, 470, colour=FAINT, sw=2)

    box(1620, 372, 200, 112, "Open Targets\nClinicalTrials.gov\nHuman Protein Atlas",
        STORE, size=13)
    arrow(1492, 640, 1614, 492, colour=FAINT)
    text(1576, 652, "egress only", size=12, anchor="middle", colour=GREY)

    arrow(1021, 362, 145, ROW - H / 2, via=[(1021, 252), (145, 252)],
          colour=EDGE, dashed=True, sw=2)
    text(560, 206, "one gene · stream held open · 2 to 4s", size=14, colour=EDGE)

    text(64, 838, "Not shown: auth, multi-tenancy, audit log persistence.", size=15,
         colour=GREY)

    box(700, 890, 190, 46, "GitHub Actions", MANAGED, size=14)
    arrow(890, 913, 926, 913)
    box(928, 890, 120, 46, "ECR", MANAGED, size=14)
    arrow(1048, 913, 1084, 913)
    box(1086, 890, 210, 46, "ECS rolling deploy", MANAGED, size=14)
    text(500, 902, "push to main", size=14, colour=GREY)
    arrow(624, 913, 696, 913, colour=FAINT)
    # The deployment path used to end in mid air. It deploys the container, so it points at it.
    arrow(1191, 936, 1191, 966, colour=FAINT, dashed=True, head=None)
    arrow(1191, 966, 876, 966, colour=FAINT, dashed=True, head=None)
    arrow(876, 966, 876, 470, colour=FAINT, dashed=True, head=None)
    arrow(876, 470, 960, 470, colour=FAINT, dashed=True, head=None)
    arrow(960, 470, 960, 448, colour=FAINT, dashed=True)

    write("architecture-aws-full")


# =======================================================================================
def build_slide():
    reset()
    ROW, H = 400, 92

    text(60, 44, "BioLead: the deployment", size=40)
    legend(1420, 52, size=18, gap=34, swatch=22)

    box(60, ROW - H / 2, 180, H, "Users", STORE, size=24)
    arrow(240, ROW, 288, ROW, sw=2)
    box(290, ROW - H / 2, 150, H, "WAF", MANAGED, size=24)
    arrow(440, ROW, 488, ROW, sw=2)
    box(490, ROW - H / 2, 230, H, "CloudFront", MANAGED, size=24)
    arrow(605, ROW + H / 2, 605, 522, sw=2)
    box(490, 524, 230, 80, "S3 · React bundle", STORE, size=21)
    arrow(720, ROW, 800, ROW, sw=2)

    box(770, 268, 800, 360, "", (GREY, NONE), ss="dashed")
    text(788, 280, "VPC", size=18, colour=GREY)

    box(796, 318, 220, 170, "", (GREY, NONE), ss="dotted")
    text(810, 328, "public subnet", size=16, colour=GREY)
    box(816, ROW - H / 2, 180, H, "ALB", MANAGED, size=24)
    text(816, 452, "no 29 second limit", size=15, colour=GREY)

    box(1046, 318, 500, 290, "", (GREY, NONE), ss="dotted")
    text(1060, 328, "private subnet", size=16, colour=GREY)

    box(1056, 338, 270, 212, "", (GREY, NONE), ss="dotted")
    box(1066, 348, 250, 96, "ECS Fargate\nFastAPI", COMPUTE, size=22)
    arrow(996, ROW, 1062, ROW, sw=2)
    box(1066, 474, 250, 62, "background task", COMPUTE, size=19, ss="dotted")
    arrow(1191, 444, 1191, 470, colour=BATCH, sw=2)
    text(1056, 558, "a list · client polls · agent off above 25 genes", size=16, colour=BATCH)

    box(1340, 338, 200, 212, "", (GREY, NONE), ss="dotted")
    box(1352, 352, 176, 46, "Bedrock", MANAGED, size=19)
    text(1352, 402, "VPC endpoint, IAM not a key", size=13, colour=GREY)
    box(1352, 440, 176, 62, "DynamoDB\nrun state + cache", STORE, size=16)
    text(1352, 506, "keyed on data version", size=13, colour=GREY)
    # ONE line, compute group to the stack.
    arrow(1326, 444, 1336, 444, colour=FAINT, sw=2)

    box(1630, 356, 190, 130, "Open Targets\nClinicalTrials\nProtein Atlas", STORE, size=19)
    arrow(1540, 420, 1626, 420, colour=FAINT)

    arrow(1290, 348, 150, ROW - H / 2, via=[(1290, 205), (150, 205)],
          colour=EDGE, dashed=True, sw=3)
    text(700, 150, "one gene · stream held open · 2 to 4s", size=20, colour=EDGE)

    text(60, 668, "Not shown: auth, multi-tenancy, audit log persistence.", size=20,
         colour=GREY)

    box(700, 760, 220, 56, "GitHub Actions", MANAGED, size=18)
    arrow(920, 788, 956, 788, sw=2)
    box(958, 760, 130, 56, "ECR", MANAGED, size=18)
    arrow(1088, 788, 1124, 788, sw=2)
    box(1126, 760, 250, 56, "ECS rolling deploy", MANAGED, size=18)
    text(500, 774, "push to main", size=18, colour=GREY)
    arrow(624, 788, 696, 788, colour=FAINT, sw=2)
    arrow(1251, 816, 1251, 858, colour=FAINT, dashed=True, sw=2, head=None)
    arrow(1251, 858, 1030, 858, colour=FAINT, dashed=True, sw=2, head=None)
    arrow(1030, 858, 1030, 428, colour=FAINT, dashed=True, sw=2, head=None)
    arrow(1030, 428, 1062, 428, colour=FAINT, dashed=True, sw=2)

    write("architecture-aws-slide")


build_full()
build_slide()
