#!/usr/bin/env python3
"""Build a signed, installable Beat Slicer APK using the Android SDK tools."""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ANDROID = ROOT / "android"
BUILD = ANDROID / "build"
DIST = ROOT / "dist"
SDK = Path(os.environ.get("ANDROID_HOME", "/opt/homebrew/share/android-commandlinetools"))
JAVA_HOME = Path(os.environ.get("JAVA_HOME", "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"))
TOOLS = SDK / "build-tools" / "35.0.0"
PLATFORM = SDK / "platforms" / "android-35" / "android.jar"
SOURCE_HTML = ROOT / "mobile" / "beat_slicer_mobile.source.html"
TRACKS = (
    ROOT / "Free_Flow_Flava_-_Mistake_79894259.mp3",
    ROOT / "QMIIR_-_YALA_Slowed_81306825.mp3",
    ROOT / "Ian_Asher_-_Take_Me_To_The_Moon_81645636.mp3",
    ROOT / "Rammstein_Andrea_Marino_-_Adieu_-_RMX_by_Andrea_Marino_76187476.mp3",
    ROOT / "DJ_Major_-_Stay_Deluxe_Edition_79729726.mp3",
    ROOT / "Qasus_-_Cyberpunk_79935030.mp3",
)


def run(*args: object) -> None:
    printable = " ".join(str(arg) for arg in args)
    print("+", printable)
    env = os.environ.copy()
    env["JAVA_HOME"] = str(JAVA_HOME)
    env["PATH"] = str(JAVA_HOME / "bin") + os.pathsep + env.get("PATH", "")
    subprocess.run([str(arg) for arg in args], check=True, env=env)


def require(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"missing build dependency: {path}")


def prepare_assets() -> Path:
    assets = BUILD / "assets"
    assets.mkdir(parents=True)
    html = SOURCE_HTML.read_text(encoding="utf-8")
    html = html.replace(
        '<div class="row">\n    <button id="bFile">',
        '<div class="row" hidden>\n    <button id="bFile">',
        1,
    )
    html = html.replace(
        '<b>Track file</b> — the most accurate sync (beats are analyzed ahead of time).\n    <br>',
        "",
        1,
    )
    (assets / "index.html").write_text(html, encoding="utf-8")
    for track in TRACKS:
        require(track)
        shutil.copy2(track, assets / track.name)
    return assets


def add_dex(apk: Path, dex: Path) -> None:
    with zipfile.ZipFile(apk, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(dex, "classes.dex")


def main() -> None:
    for path in (PLATFORM, TOOLS / "aapt2", TOOLS / "d8", TOOLS / "zipalign", TOOLS / "apksigner", JAVA_HOME / "bin" / "javac"):
        require(path)

    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    DIST.mkdir(parents=True, exist_ok=True)
    assets = prepare_assets()

    compiled = BUILD / "compiled.zip"
    unsigned = BUILD / "unsigned.apk"
    classes = BUILD / "classes"
    classes_jar = BUILD / "classes.jar"
    dex_dir = BUILD / "dex"
    classes.mkdir()
    dex_dir.mkdir()

    run(TOOLS / "aapt2", "compile", "--dir", ANDROID / "res", "-o", compiled)
    run(
        TOOLS / "aapt2", "link", "-o", unsigned, "-I", PLATFORM,
        "--manifest", ANDROID / "AndroidManifest.xml", "--min-sdk-version", "24",
        "--target-sdk-version", "35", "--version-code", "6", "--version-name", "1.4.0",
        "-A", assets, compiled,
    )
    run(
        JAVA_HOME / "bin" / "javac", "-source", "8", "-target", "8", "-encoding", "UTF-8",
        "-classpath", PLATFORM, "-d", classes,
        ANDROID / "src" / "com" / "beatslicer" / "game" / "MainActivity.java",
    )
    run(JAVA_HOME / "bin" / "jar", "cf", classes_jar, "-C", classes, ".")
    run(TOOLS / "d8", "--lib", PLATFORM, "--min-api", "24", "--output", dex_dir, classes_jar)
    add_dex(unsigned, dex_dir / "classes.dex")

    aligned = BUILD / "aligned.apk"
    output = DIST / "BeatSlicer-android.apk"
    run(TOOLS / "zipalign", "-f", "-p", "4", unsigned, aligned)

    keystore = ANDROID / ".debug.keystore"
    if not keystore.exists():
        run(
            JAVA_HOME / "bin" / "keytool", "-genkeypair", "-v", "-keystore", keystore,
            "-storepass", "android", "-alias", "androiddebugkey", "-keypass", "android",
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-dname", "CN=Beat Slicer Debug,O=Beat Slicer,C=RU",
        )
    run(
        TOOLS / "apksigner", "sign", "--ks", keystore, "--ks-key-alias", "androiddebugkey",
        "--ks-pass", "pass:android", "--key-pass", "pass:android", "--out", output, aligned,
    )
    run(TOOLS / "apksigner", "verify", "--verbose", output)
    print(f"built {output} ({output.stat().st_size / 1024 / 1024:.2f} MiB)")


if __name__ == "__main__":
    main()
