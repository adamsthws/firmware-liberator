// The stock firmware's EasyFlash V4 store on a Beken device, read raw: its
// Wi-Fi (wifi-bk72xx.yaml) and a meter's factory calibration (the S61's
// elecCalibCoef). Scanned across 0x3e0000-0x3ec000, which holds it on the 4 MB
// layout of each Beken device converted to ESPHome (at 0x3e2000 on the S61's),
// where it stays as it is so the device can always go back to stock.
#pragma once

#include <libretiny.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace autoflash {

// zlib's CRC-32.
inline uint32_t ef40_crc32(const uint8_t *p, size_t n) {
  uint32_t crc = 0xffffffff;
  for (size_t i = 0; i < n; i++) {
    crc ^= p[i];
    for (int b = 0; b < 8; b++) crc = (crc & 1) ? (crc >> 1) ^ 0xEDB88320 : (crc >> 1);
  }
  return ~crc;
}

// One node: "KV40", len u32, crc32 u32, name_len u8 + 0xff*3, value_len u32,
// name, value; the crc covers the name_len field through the end of the value.
// A key is rewritten by appending, so the last valid node wins. Scanned in
// 4 KB windows that overlap by more than a node.
inline bool ef40_read(const char *key, std::string &out) {
  const uint32_t start = 0x3e0000, end = 0x3ec000, window = 4096, overlap = 128;
  const size_t key_len = strlen(key);
  std::vector<uint8_t> win(window);
  bool found = false;
  for (uint32_t addr = start; addr < end; addr += window - overlap) {
    uint32_t want = std::min<uint32_t>(window, end - addr);
    if (lt_flash_read(addr, win.data(), want) != want) continue;
    for (uint32_t i = 0; i + 20 <= want; i++) {
      if (memcmp(win.data() + i, "KV40", 4) != 0) continue;
      uint32_t name_len = win[i + 12], value_len, stored;
      memcpy(&value_len, win.data() + i + 16, 4);
      memcpy(&stored, win.data() + i + 8, 4);
      if (name_len != key_len || value_len == 0 || value_len >= 128) continue;
      uint32_t name_at = i + 20, value_at = name_at + name_len;
      if (value_at + value_len > want) continue;  // straddles: the next window has it
      if (memcmp(win.data() + name_at, key, key_len) != 0) continue;
      if (ef40_crc32(win.data() + i + 12, value_at + value_len - (i + 12)) != stored) continue;
      out.assign((const char *) win.data() + value_at, value_len);
      found = true;
    }
  }
  return found;
}

}  // namespace autoflash
