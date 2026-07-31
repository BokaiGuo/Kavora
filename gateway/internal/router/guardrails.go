package router

import "time"

type Mode string

const (
	ModeStatic   Mode = "static"
	ModeShadow   Mode = "shadow"
	ModeEnforced Mode = "enforced"
)

type Guardrails struct {
	Mode               Mode
	MaxTrafficFraction float64
	Cooldown           time.Duration
	changed            time.Time
}

func NewGuardrails() Guardrails {
	return Guardrails{Mode: ModeStatic, MaxTrafficFraction: 0.1, Cooldown: time.Minute}
}
func (g *Guardrails) SetMode(mode Mode, now time.Time) bool {
	if mode == g.Mode || now.Sub(g.changed) < g.Cooldown {
		return false
	}
	g.Mode, g.changed = mode, now
	return true
}
func (g Guardrails) AllowsEnforced(sample float64) bool {
	return g.Mode == ModeEnforced && sample >= 0 && sample <= g.MaxTrafficFraction
}
