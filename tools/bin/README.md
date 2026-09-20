# tools/bin

`ffmpeg` here is a native arm64 static build. The toolkit needs it because the
x86_64 ffmpeg in /usr/local/bin runs under Rosetta on this machine and cannot
open the HEVC hardware encoder, which forces software encoding at about 6
frames per second for 4K60.

The binary is not in git. To fetch it again:

    curl -sL -o /tmp/ff.zip https://www.osxexperts.net/ffmpeg9arm.zip
    unzip -o -j /tmp/ff.zip ffmpeg -d tools/bin
    chmod +x tools/bin/ffmpeg

Then check that the hardware encoder opens:

    tools/bin/ffmpeg -f lavfi -i testsrc=size=3840x2160:rate=60:duration=1 \
        -c:v hevc_videotoolbox -b:v 50M -f null -
