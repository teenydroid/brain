import { ImageResponse } from "next/og"
import { Orb } from "./icon"

export const size = { width: 180, height: 180 }
export const contentType = "image/png"

/** The home-screen icon: the same orb, bigger. */
export default function AppleIcon() {
  return new ImageResponse(<Orb size={size.width} />, size)
}
