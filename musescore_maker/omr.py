"""Optical music recognition: page image -> MusicXML.

Default engine is homr (deep-learning, copes well with handwritten-style jazz fonts and photos).
Audiveris is available as an alternative (better on clean engraved print with many staves).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

AUDIVERIS = Path(os.environ.get("AUDIVERIS", r"C:\Program Files\Audiveris\Audiveris.exe"))

_homr_ready = False


def _fields(cls) -> set[str]:
    import inspect

    return set(inspect.signature(cls).parameters)


def _homr_setup():
    global _homr_ready
    from homr import main as hm

    if not _homr_ready:
        import onnxruntime as ort

        ort.set_default_logger_severity(3)
        hm.download_weights(segnet_use_gpu=False, transformer_use_gpu=False, coreml_encoder=False)
        _homr_ready = True
    config = hm.ProcessingConfig(
        enable_debug=False,
        enable_cache=False,
        write_staff_positions=True,  # used by multirest.py
        read_staff_positions=False,
        selected_staff=-1,
        transformer_use_gpu=False,
        segnet_use_gpu=False,
        coreml_encoder=False,
        **({"title_detection": False} if "title_detection" in _fields(hm.ProcessingConfig) else {}),  # homr >= 0.7
    )
    from homr.music_xml_generator import XmlGeneratorArguments

    return hm, config, XmlGeneratorArguments(False, None, None)


def run_homr(image: Path, out_xml: Path) -> bool:
    """Returns False when the page has no music (cover pages, blank pages)."""
    hm, config, xml_args = _homr_setup()
    try:
        hm.process_image(str(image), config, xml_args)
    except Exception as e:  # homr raises plain Exceptions for "No noteheads found" etc.
        print(f"    homr: no music on {image.name} ({e})")
        return False
    produced = image.with_suffix(".musicxml")
    if not produced.exists():
        return False
    if produced != out_xml:
        shutil.move(produced, out_xml)
    staffs = image.with_suffix(".txt")
    if staffs.exists():
        shutil.move(staffs, staff_file(out_xml))
    return True


def staff_file(out_xml: Path) -> Path:
    return out_xml.with_suffix(".staffs.txt")


def run_audiveris(image: Path, out_xml: Path) -> bool:
    out_dir = out_xml.parent / "audiveris"
    out_dir.mkdir(exist_ok=True)
    subprocess.run(
        [str(AUDIVERIS), "-batch", "-transcribe", "-export", "-output", str(out_dir), "--", str(image)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    mxl = out_dir / (image.stem + ".mxl")
    if not mxl.exists():
        return False
    import zipfile

    with zipfile.ZipFile(mxl) as z:
        name = next(n for n in z.namelist() if n.endswith(".xml") and not n.startswith("META-INF"))
        out_xml.write_bytes(z.read(name))
    return True


def recognise(image: Path, out_xml: Path, engine: str = "homr") -> bool:
    if out_xml.exists():
        return True
    empty_marker = out_xml.with_suffix(".empty")
    if empty_marker.exists():
        return False
    ok = run_audiveris(image, out_xml) if engine == "audiveris" else run_homr(image, out_xml)
    if not ok:
        empty_marker.touch()
    return ok
