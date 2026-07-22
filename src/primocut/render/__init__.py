"""Phase 3 clip rendering: section download (yt-dlp) + fine cut (FFmpeg/NVENC).

Kept as thin subprocess wrappers (a `runner` seam) so the tests can fake yt-dlp
and FFmpeg entirely — no network, no GPU. The section is an EPHEMERAL
intermediate (a throwaway temp dir, like the audio); the clip is the permanent
deliverable.
"""
