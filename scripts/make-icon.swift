// Draws the Hangshot app icon: a screenshot card with crop corners, hanging
// from an amber hook, on a deep navy tile.
// Usage: swift scripts/make-icon.swift out.png
import AppKit

let size: CGFloat = 1024
let out = CommandLine.arguments.dropFirst().first ?? "icon.png"

let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size),
                           bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                           colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let ctx = NSGraphicsContext.current!.cgContext

func color(_ hex: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat(hex >> 16 & 0xFF) / 255, green: CGFloat(hex >> 8 & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: a)
}

func shadow(_ alpha: CGFloat, blur: CGFloat, y: CGFloat) {
    let s = NSShadow()
    s.shadowColor = NSColor.black.withAlphaComponent(alpha)
    s.shadowBlurRadius = blur
    s.shadowOffset = NSSize(width: 0, height: y)
    s.set()
}

let amber = color(0xFFB21E)

// Tile, on the macOS icon grid: 824 square with a 100 margin.
let tile = NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824), xRadius: 185, yRadius: 185)
ctx.saveGState()
shadow(0.35, blur: 28, y: -14)
color(0x16213D).setFill()
tile.fill()
ctx.restoreGState()

ctx.saveGState()
tile.addClip()
NSGradient(colors: [color(0x2B3F6C), color(0x121A31)])!.draw(in: tile, angle: -90)
// A soft glow behind the card.
NSGradient(colors: [color(0x5B7BD5, 0.45), color(0x5B7BD5, 0)])!
    .draw(fromCenter: NSPoint(x: 512, y: 470), radius: 0, toCenter: NSPoint(x: 512, y: 470), radius: 380, options: [])
ctx.restoreGState()

// The hook: a short stem from the top, curling into the eye the card hangs from.
let hook = NSBezierPath()
hook.move(to: NSPoint(x: 512, y: 860))
hook.line(to: NSPoint(x: 512, y: 760))
hook.curve(to: NSPoint(x: 512, y: 640), controlPoint1: NSPoint(x: 512, y: 700), controlPoint2: NSPoint(x: 590, y: 640))
hook.curve(to: NSPoint(x: 450, y: 700), controlPoint1: NSPoint(x: 470, y: 640), controlPoint2: NSPoint(x: 450, y: 665))
hook.lineWidth = 30
hook.lineCapStyle = .round
ctx.saveGState()
shadow(0.35, blur: 10, y: -4)
amber.setStroke()
hook.stroke()
ctx.restoreGState()

// The card, tilted a little as if it just swung onto the hook.
ctx.saveGState()
ctx.translateBy(x: 512, y: 645)
ctx.rotate(by: -5 * .pi / 180)
let card = NSRect(x: -270, y: -400, width: 540, height: 400)
let cardPath = NSBezierPath(roundedRect: card, xRadius: 34, yRadius: 34)
ctx.saveGState()
shadow(0.45, blur: 36, y: -18)
color(0xF7F8FB).setFill()
cardPath.fill()
ctx.restoreGState()

// The photo inside: sky, sun and two hills.
let photo = card.insetBy(dx: 30, dy: 30)
let photoPath = NSBezierPath(roundedRect: photo, xRadius: 14, yRadius: 14)
ctx.saveGState()
photoPath.addClip()
NSGradient(colors: [color(0xBFD7FF), color(0x8FB4F5)])!.draw(in: photo, angle: -90)
color(0xFFD36B).setFill()
NSBezierPath(ovalIn: NSRect(x: photo.maxX - 150, y: photo.maxY - 140, width: 86, height: 86)).fill()
let back = NSBezierPath()
back.move(to: NSPoint(x: photo.minX, y: photo.minY + 90))
back.curve(to: NSPoint(x: photo.maxX, y: photo.minY + 60),
           controlPoint1: NSPoint(x: photo.minX + 160, y: photo.minY + 250),
           controlPoint2: NSPoint(x: photo.maxX - 200, y: photo.minY + 40))
back.line(to: NSPoint(x: photo.maxX, y: photo.minY))
back.line(to: NSPoint(x: photo.minX, y: photo.minY))
back.close()
color(0x4F7BD9).setFill()
back.fill()
let front = NSBezierPath()
front.move(to: NSPoint(x: photo.minX, y: photo.minY + 30))
front.curve(to: NSPoint(x: photo.maxX, y: photo.minY + 130),
            controlPoint1: NSPoint(x: photo.minX + 220, y: photo.minY - 20),
            controlPoint2: NSPoint(x: photo.maxX - 140, y: photo.minY + 200))
front.line(to: NSPoint(x: photo.maxX, y: photo.minY))
front.line(to: NSPoint(x: photo.minX, y: photo.minY))
front.close()
color(0x2C4FA3).setFill()
front.fill()
ctx.restoreGState()

// Crop corners: what makes it a screenshot rather than a photo.
let corner: CGFloat = 70, inset: CGFloat = 54
let marks = NSBezierPath()
let r = card.insetBy(dx: -inset + 30, dy: -inset + 30)
for (x, y, dx, dy) in [(r.minX, r.minY, 1.0, 1.0), (r.maxX, r.minY, -1.0, 1.0),
                       (r.minX, r.maxY, 1.0, -1.0), (r.maxX, r.maxY, -1.0, -1.0)] {
    marks.move(to: NSPoint(x: x + dx * corner, y: y))
    marks.line(to: NSPoint(x: x, y: y))
    marks.line(to: NSPoint(x: x, y: y + dy * corner))
}
marks.lineWidth = 18
marks.lineCapStyle = .round
marks.lineJoinStyle = .round
amber.setStroke()
marks.stroke()
ctx.restoreGState()

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
