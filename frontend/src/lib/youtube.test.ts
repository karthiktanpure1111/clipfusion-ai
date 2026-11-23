import { describe, expect, it } from "vitest";
import { parseYouTubeUrl } from "./youtube";

describe("parseYouTubeUrl", () => {
  it.each([
    ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"],
    ["m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"],
    ["https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://www.youtube.com/live/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"],
    ["  http://youtu.be/dQw4w9WgXcQ  ", "dQw4w9WgXcQ"],
  ])("accepts %s", (url, id) => {
    expect(parseYouTubeUrl(url)).toBe(id);
  });

  it.each([
    "",
    "not a url",
    "https://www.youtube.com/",
    "https://www.youtube.com/watch",
    "https://www.youtube.com/watch?v=short",
    "https://www.youtube.com/channel/UC1234567890",
    "https://www.youtube.com/playlist?list=PL123",
    "https://youtu.be/",
    "https://evil.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
    "ftp://youtu.be/dQw4w9WgXcQ",
  ])("rejects %s", (url) => {
    expect(parseYouTubeUrl(url)).toBeNull();
  });
});
