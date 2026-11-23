# ClipFusion AI — Product Requirements (verbatim from the project owner)

Build a complete AI-powered video clipping and social media automation SaaS platform that converts long
videos, YouTube videos, gaming streams, livestream recordings, podcasts, interviews and other authorised
video files into engaging YouTube Shorts and Instagram Reels.

The platform must automatically: (1) analyse the entire video, (2) find interesting and engaging moments,
(3) generate short clips, (4) automatically edit them into vertical videos, (5) generate subtitles,
(6) allow users to customise subtitle styles, (7) allow users to select the exact visible area of the video,
(8) ensure streamers' faces and important content remain visible, (9) generate titles, captions and
hashtags, (10) schedule and automatically publish clips to connected social media accounts.

## 1. Video source input
Three options: paste a YouTube video URL; upload an original video from the computer; import an authorised
video from cloud storage. For YouTube links, retrieve permitted video metadata and supported analytics
through official APIs. Do not implement unauthorised video downloading or bypass platform restrictions.
Actual video processing must use an uploaded original file or another authorised source. Include a
permission confirmation before processing and publishing.

## 2. AI video analysis and highlight detection
Analyse: interesting conversations, funny moments, exciting moments, strong statements, emotional
reactions, important explanations, surprising moments, gaming achievements, streamer reactions,
interesting audience interactions. Use: speech-to-text transcription, audio analysis, scene-change
detection, transcript segmentation, video frame analysis, speech and silence detection, highlight scoring.
Use available YouTube audience-retention analytics when authorised. Never present estimated AI highlight
scores as actual viewership or retention statistics. Display: start timestamp, end timestamp, duration,
highlight score, reason for selection, transcript preview.

## 3. Automatic video clipping
Generate multiple clips from one long video. Durations: 15 / 30 / 45 / 60 / 90 seconds / custom.
Each clip should start naturally, end naturally, preserve the context of the conversation, avoid cutting
off sentences, avoid unnecessary silence, focus on the interesting moment. Prevent duplicate and heavily
overlapping clips.

## 4. Custom video framing and smart crop editor (ONE OF THE MOST IMPORTANT FEATURES)
The streamer/speaker/person's face must never get cropped out when converting landscape to vertical 9:16.
A gaming streamer may have gameplay occupying most of the screen, a small facecam window in one corner and
important gameplay elements elsewhere — both streamer AND relevant gameplay must remain visible. Do not
blindly crop the centre.

A. Manual area selection — interactive framing editor: select the exact area to retain; drag the visible
region; resize the selection; position it anywhere; zoom in/out; preview the crop in real time; save
framing preferences; apply the same framing to multiple clips. Draggable + resizable selection rectangle
over the original video preview. Display the final 9:16 output preview alongside the original video. Make
clear which parts are visible and which are outside the final frame.

B. AI face detection and tracking — detect the streamer's/speaker's face, identify important people, track
positions throughout the clip, keep faces inside the final frame, avoid cutting off heads and faces, adjust
framing when the person moves, avoid sudden distracting camera movements. Use OpenCV / MediaPipe or
similar. Do not assume the face is in the centre.

C. Framing modes — Mode 1 AI Smart Crop (track main speaker/streamer); Mode 2 Manual Crop; Mode 3
Facecam + Gameplay (gameplay main background, facecam visible section, adjustable facecam position and
size, custom split-screen layouts); Mode 4 Picture-in-Picture (main video + draggable/resizable facecam
overlay); Mode 5 Full Frame / No Crop (fit inside 9:16 with blurred or selectable background). Never cut
off a person simply to fill the vertical screen.

D. Multi-person tracking — detect multiple faces, let users select the primary speaker, keep the selected
person visible, allow automatic speaker switching when appropriate, avoid jumping between faces
unnecessarily.

E. Crop protection and warnings — before rendering analyse the final crop. If a face is outside the
selected area, a head is cut off, the streamer is hidden or important content is outside the frame:
display a warning and offer framing corrections. Do not silently crop out the streamer. Allow preview of
the final result before exporting.

F. Export — 9:16, 1080x1920 where supported, MP4, high quality, correct audio sync. Must work for gaming
streams, podcasts, interviews, reactions and ordinary landscape videos.

## 5. Customisable subtitle studio
A. Automatic generation (Whisper / faster-whisper): transcription, word-level timestamps, sentence-level
subtitles, synchronisation, multiple languages, punctuation correction, editable text, regenerate;
users can correct misrecognised words.
B. Fonts: Arial, Roboto, Poppins, Montserrat, Inter, Open Sans, Anton, Bebas Neue, Oswald, Impact, others;
upload custom font files where licensing permits.
C. Font size: slider, presets (small/medium/large/extra-large), individual subtitle adjustments.
D. Colours/appearance: text colour, highlight colour, background colour, background opacity, outline
colour, outline thickness, shadow, bold, italic, letter spacing, line spacing, alignment; colour picker.
E. Positioning: top, upper-middle, centre, lower-middle, bottom, custom; draggable subtitle preview; keep
inside the safe area and do not cover the streamer's face or important gameplay.
F. Animations: word-by-word highlighting, karaoke, pop-up, smooth fade-in, typewriter, important-word
colour highlighting, dynamic emphasis, static; animation speed and highlighting behaviour.
G. Templates with live preview: Gaming, Podcast, Minimal, Bold Viral, Professional, Reaction, Custom.
H. Editor: edit words/sentences, adjust start/end times, change individual subtitle colours, change font
size of selected words, highlight specific words, split long captions, merge segments, real-time preview.
I. Saved presets (e.g. "My Gaming Captions") applicable to future clips.
Subtitles are burned into the final video on export while remaining editable before rendering.

## 6. AI video editing (FFmpeg)
Automatic vertical formatting, smart cropping, face tracking, background blur, subtitle rendering, audio
normalisation, optional silence removal, optional intro and outro, optional watermark, audio sync,
high-quality MP4 export — all respecting the custom framing and subtitle settings.

## 7. AI titles, captions and hashtags
Per clip: engaging title, description, social caption, relevant hashtags, opening hook. Different metadata
for YouTube Shorts and Instagram Reels. Everything editable before publishing.

## 8. Social media integration (official only)
YouTube: YouTube Data API, Google OAuth, official upload endpoint, resumable uploads, title/description,
tags and privacy, publishing status. Instagram: Instagram Graph API, Meta OAuth, professional-account
publishing, Reels, caption/hashtags, publishing status tracking. Never request passwords or use unofficial
methods. Clearly explain API permissions, app review, account eligibility and other requirements.

## 9. Automatic publishing scheduler
Every 1/2/3/6 hours, custom time, specific days, platform-specific schedules. Review mode (approval before
scheduling) and Automatic mode (approved settings allow clips to enter the queue and publish without
manual intervention). Publishing queue, persistent scheduler, retry handling, duplicate prevention,
pause/resume, failed upload tracking, daily publishing limits, next-publishing-time display, user timezone.

## 10. Interactive dashboard — pages
Dashboard, Add New Video, AI Processing, Generated Clips, Smart Framing Editor, Subtitle Studio, Publishing
Calendar, Connected Accounts, Analytics, Settings. Dashboard shows: total videos, total generated clips,
processing videos, clips awaiting approval, scheduled posts, successfully published clips, failed uploads,
next publishing time; interactive charts, video previews, status indicators, publishing calendar.

## 11. UI/UX and colour design
Premium interactive SaaS combining YouTube and Instagram branding. YouTube: #FF0033 #0F0F12 #FFFFFF.
Instagram: #8134AF #DD2A7B #FCAF45 #515BD4. Dark charcoal background, YouTube-red primary buttons,
Instagram gradient accents, modern rounded cards, subtle glassmorphism, smooth animations, interactive
previews, attractive video cards, responsive layouts, modern icons, professional navigation sidebar.
Landing heading: "Turn Long Videos Into Viral Shorts Automatically." Subtitle: "AI-powered clipping, smart
framing, custom captions, and automatic publishing — all in one place." Must look like a high-quality video
editing and automation SaaS, not a basic admin panel.

## 12. Stack
Frontend: Next.js, React, TypeScript, Tailwind CSS, shadcn/ui, Framer Motion, Recharts.
Backend: Python, FastAPI, REST, SQLAlchemy. Video: FFmpeg, OpenCV, PySceneDetect. AI: Whisper /
faster-whisper, MediaPipe or equivalent, pretrained audio/video models, LLM for highlight analysis.
PostgreSQL. Redis + Celery. S3-compatible storage / Cloudflare R2. Secure auth, Google OAuth, Meta OAuth.

## 13. Backend modules
Authentication, user management, video upload, source metadata, video analysis, audio transcription,
highlight detection, face detection, face tracking, smart framing, video clipping, video rendering,
subtitle generation, subtitle editing, social publishing, scheduling, background workers, analytics,
notifications.

## 14. Database tables
Users, connected accounts, video sources, processing jobs, transcripts, generated clips, clip framing
settings, subtitle styles, saved subtitle presets, publishing schedules, publishing queue, publishing
history, analytics, notifications — with relationships, indexing and user-level access control.

## 15. Security and reliability
OAuth token encryption, secure file uploads, file validation, user authentication, protected endpoints,
background job persistence, processing retries, duplicate prevention, error handling, logging, temporary
file cleanup, secure media storage, rate limiting, API quota management. Never hardcode credentials.
Complete .env.example.

## 16. Process
Inspect the existing project; do not overwrite functional code unnecessarily. Implement and test each
feature. Do not create fake functionality or display simulated results as real AI processing. If external
API credentials or approvals are missing, implement the integration structure and clearly explain what is
required.

## Most important
Never automatically crop out the streamer's face. Always provide manual control over framing. Support
facecam + gameplay layouts. Fully customisable subtitle editor. Saved framing and subtitle presets.
Real-time preview before export. Actual automated publishing using official APIs.
