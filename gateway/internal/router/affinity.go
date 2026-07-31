package router

import (
	"container/list"
	"sync"
	"time"
)

type affinityEntry struct {
	key, tenant, backend string
	expires              time.Time
}
type Affinity struct {
	mu     sync.Mutex
	max    int
	ttl    time.Duration
	values map[string]*list.Element
	order  *list.List
}

func NewAffinity(max int, ttl time.Duration) *Affinity {
	if max <= 0 {
		max = 1024
	}
	if ttl <= 0 {
		ttl = 5 * time.Minute
	}
	return &Affinity{max: max, ttl: ttl, values: map[string]*list.Element{}, order: list.New()}
}
func (a *Affinity) Get(tenant, key string, now time.Time) (string, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	element, ok := a.values[tenant+"\x00"+key]
	if !ok {
		return "", false
	}
	entry := element.Value.(affinityEntry)
	if !now.Before(entry.expires) {
		delete(a.values, entry.key)
		a.order.Remove(element)
		return "", false
	}
	a.order.MoveToFront(element)
	return entry.backend, true
}
func (a *Affinity) Put(tenant, key, backend string, now time.Time) {
	a.mu.Lock()
	defer a.mu.Unlock()
	composite := tenant + "\x00" + key
	if old, ok := a.values[composite]; ok {
		old.Value = affinityEntry{composite, tenant, backend, now.Add(a.ttl)}
		a.order.MoveToFront(old)
		return
	}
	element := a.order.PushFront(affinityEntry{composite, tenant, backend, now.Add(a.ttl)})
	a.values[composite] = element
	for len(a.values) > a.max {
		oldest := a.order.Back()
		entry := oldest.Value.(affinityEntry)
		delete(a.values, entry.key)
		a.order.Remove(oldest)
	}
}
func (a *Affinity) InvalidateBackend(backend string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for key, element := range a.values {
		if element.Value.(affinityEntry).backend == backend {
			delete(a.values, key)
			a.order.Remove(element)
		}
	}
}
func (a *Affinity) Len() int { a.mu.Lock(); defer a.mu.Unlock(); return len(a.values) }
