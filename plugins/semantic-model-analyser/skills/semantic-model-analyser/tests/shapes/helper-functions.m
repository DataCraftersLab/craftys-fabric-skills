(input as text, optional width as number) as text =>
let
    trimmed = Text.Trim(input),
    padded = Text.PadStart(trimmed, width ?? 10, "0")
in
    padded
