# Test fixtures

`gradient.png` is written by Mote's own PNG encoder: a 16x16 image whose red
channel follows x, green follows y and blue follows `x xor y`, so every
decoding mistake (a wrong filter, a transposed row, a bad palette lookup)
shows up as a pixel that does not fit the pattern.

`gradient.jpg` and `gradient.gif` are the same image converted by macOS
`sips`, so the decoders are tested against files this project did not write.
