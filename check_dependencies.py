# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 CrimLawTech LLC
import sys
import subprocess
import importlib.util
import os

from ffmpeg_tools import tool_path

def check_module(module_name):
    """Check if a module is installed"""
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False

def check_ffmpeg():
    """Check if FFmpeg is installed and available in PATH"""
    try:
        process = subprocess.run([tool_path("ffmpeg"), "-version"], 
                                stdout=subprocess.PIPE, 
                                stderr=subprocess.PIPE, 
                                text=True)
        if process.returncode == 0:
            return True, process.stdout.split('\n')[0]  # Return version info
        return False, None
    except FileNotFoundError:
        return False, None

def check_ffprobe():
    """Check if FFprobe is installed and available in PATH"""
    try:
        process = subprocess.run([tool_path("ffprobe"), "-version"], 
                                stdout=subprocess.PIPE, 
                                stderr=subprocess.PIPE, 
                                text=True)
        if process.returncode == 0:
            return True, process.stdout.split('\n')[0]  # Return version info
        return False, None
    except FileNotFoundError:
        return False, None

def quick_check():
    """Check and report what dependencies are installed without prompting to install"""
    print("OSVEV dependency status:\n")
    
    # Check Python version
    python_version = sys.version.split()[0]
    print(f"Python: {python_version}")
    
    # Check PyQt6 (no fallback to PyQt5 now)
    pyqt6_installed = check_module("PyQt6")
    
    if pyqt6_installed:
        print(f"PyQt6: INSTALLED (required for high DPI support)")
    else:
        print("PyQt6: NOT INSTALLED (required)")
    
    # Check other Python modules
    for module in ["cv2", "numpy", "av"]:
        if check_module(module):
            if module == "cv2":
                print("OpenCV: INSTALLED")
            elif module == "av":
                print("PyAV: INSTALLED (reproducible enhancements and verified input)")
            else:
                print(f"{module}: INSTALLED")
        else:
            if module == "cv2":
                print(f"OpenCV: NOT INSTALLED")
            else:
                print(f"{module}: NOT INSTALLED")
    
    # Check FFmpeg and FFprobe
    ffmpeg_installed, ffmpeg_version = check_ffmpeg()
    if ffmpeg_installed:
        print(f"FFmpeg: INSTALLED - {ffmpeg_version}")
    else:
        print("FFmpeg: NOT INSTALLED")
    
    ffprobe_installed, ffprobe_version = check_ffprobe()
    if ffprobe_installed:
        print(f"FFprobe: INSTALLED - {ffprobe_version}")
    else:
        print("FFprobe: NOT INSTALLED")
    
    # Overall assessment
    print("\nSummary:")
    if pyqt6_installed and all(check_module(module) for module in ("cv2", "numpy", "av")) and ffmpeg_installed and ffprobe_installed:
        print("All required dependencies are installed!")
    else:
        print("Some dependencies are missing. Please install required components.")

if __name__ == "__main__":
    quick_check()