import { ImageResponse } from "next/og"

export const size = { width: 64, height: 64 }
export const contentType = "image/png"

/** The tab icon: a small neon orb, the blob at the centre of the page in miniature. */
export default function Icon() {
  return new ImageResponse(<Orb size={size.width} />, size)
}

export function Orb({ size }: { size: number }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#06060b",
        borderRadius: size * 0.22,
      }}
    >
      <div
        style={{
          width: size * 0.74,
          height: size * 0.74,
          borderRadius: "50%",
          background:
            "radial-gradient(circle at 70% 25%, #f3e600 0%, rgba(243,230,0,0) 38%), radial-gradient(circle at 25% 60%, #19f0ff 0%, rgba(25,240,255,0) 55%), radial-gradient(circle at 80% 80%, #ff2a6d 0%, rgba(255,42,109,0) 60%), #0b0b14",
          boxShadow: `0 0 ${size * 0.12}px rgba(25,240,255,0.7)`,
        }}
      />
    </div>
  )
}
