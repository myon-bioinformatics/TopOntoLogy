import Std

/-!
Meaning of the property witnesses returned by topontology.evaluate_properties.
Relations are the stored edges after selecting a relation or a class. In
particular, the implemented transitive check requires chords only when the
first and last vertices differ; it does not require self-loops.
Python tests connect these predicates to the actual witness dictionaries.
-/

namespace TopOntoLogy

universe u v w

abbrev EdgeRelation (Node : Type u) := Node → Node → Prop

def Symmetric {Node : Type u} (edges : EdgeRelation Node) : Prop :=
  ∀ a b, edges a b → edges b a

def Transitive {Node : Type u} (edges : EdgeRelation Node) : Prop :=
  ∀ a b c, edges a b → edges b c → edges a c

/-- The off-diagonal chord condition implemented by transitivity_witness. -/
def ChordClosed {Node : Type u} (edges : EdgeRelation Node) : Prop :=
  ∀ a b c, edges a b → edges b c → a ≠ c → edges a c

def ReflexiveClosure {Node : Type u} (edges : EdgeRelation Node) : EdgeRelation Node :=
  fun a b => a = b ∨ edges a b

theorem symmetry_witness_refutes {Node : Type u} (edges : EdgeRelation Node)
    (a b : Node) (present : edges a b) (missing : ¬ edges b a) :
    ¬ Symmetric edges := by
  intro symmetric
  exact missing (symmetric a b present)

theorem chord_witness_refutes {Node : Type u} (edges : EdgeRelation Node)
    (a b c : Node) (first : edges a b) (second : edges b c)
    (distinct : a ≠ c) (missing : ¬ edges a c) : ¬ ChordClosed edges := by
  intro closed
  exact missing (closed a b c first second distinct)

/-- This is the precise transitivity contract; ordinary transitivity is stronger. -/
theorem chord_closed_iff_reflexive_closure_transitive {Node : Type u}
    (edges : EdgeRelation Node) :
    ChordClosed edges ↔ Transitive (ReflexiveClosure edges) := by
  constructor
  · intro closed a b c first second
    rcases first with equal | edge
    · subst b
      exact second
    · rcases second with equal | next
      · subst c
        exact Or.inr edge
      · classical
        by_cases same : a = c
        · exact Or.inl same
        · exact Or.inr (closed a b c edge next same)
  · intro transitive a b c first second distinct
    rcases transitive a b c (Or.inr first) (Or.inr second) with same | chord
    · exact False.elim (distinct same)
    · exact chord

theorem chord_witness_refutes_closure {Node : Type u} (edges : EdgeRelation Node)
    (a b c : Node) (first : edges a b) (second : edges b c)
    (distinct : a ≠ c) (missing : ¬ edges a c) :
    ¬ Transitive (ReflexiveClosure edges) := by
  intro transitive
  exact chord_witness_refutes edges a b c first second distinct missing
    ((chord_closed_iff_reflexive_closure_transitive edges).mpr transitive)

def Inverse {Node : Type u} (subject partner : EdgeRelation Node) : Prop :=
  ∀ a b, subject a b ↔ partner b a

/-- Either side of inverse_of can supply the unmatched edge. -/
theorem inverse_witness_refutes {Node : Type u} (subject partner : EdgeRelation Node)
    (a b : Node)
    (witness : (subject a b ∧ ¬ partner b a) ∨ (partner a b ∧ ¬ subject b a)) :
    ¬ Inverse subject partner := by
  intro inverse
  rcases witness with ⟨present, missing⟩ | ⟨present, missing⟩
  · exact missing ((inverse a b).mp present)
  · exact missing ((inverse b a).mpr present)

def Endpoint {Node : Type u} (edges : EdgeRelation Node) (node : Node) : Prop :=
  (∃ other, edges node other) ∨ (∃ other, edges other node)

/-- disjoint_with means disjoint incident vertices, not merely distinct edges. -/
def DisjointEndpoints {Node : Type u} (left right : EdgeRelation Node) : Prop :=
  ∀ node, ¬ (Endpoint left node ∧ Endpoint right node)

theorem endpoint_witness_refutes {Node : Type u} (left right : EdgeRelation Node)
    (a b c d node : Node) (leftEdge : left a b) (rightEdge : right c d)
    (onLeft : node = a ∨ node = b) (onRight : node = c ∨ node = d) :
    ¬ DisjointEndpoints left right := by
  have inLeft : Endpoint left node := by
    rcases onLeft with equal | equal
    · subst node
      exact Or.inl ⟨b, leftEdge⟩
    · subst node
      exact Or.inr ⟨a, leftEdge⟩
  have inRight : Endpoint right node := by
    rcases onRight with equal | equal
    · subst node
      exact Or.inl ⟨d, rightEdge⟩
    · subst node
      exact Or.inr ⟨c, rightEdge⟩
  intro disjoint
  exact disjoint node ⟨inLeft, inRight⟩

def ClassRelation {Node : Type u} {Label : Type v} {Category : Type w}
    (edges : Label → EdgeRelation Node) (classes : Label → Option Category)
    (category : Category) : EdgeRelation Node :=
  fun a b => ∃ label, classes label = some category ∧ edges label a b

/-- Pooling symmetric relations preserves symmetry in their selected class. -/
theorem class_union_symmetric {Node : Type u} {Label : Type v} {Category : Type w}
    (edges : Label → EdgeRelation Node) (classes : Label → Option Category)
    (category : Category)
    (each : ∀ label, classes label = some category → Symmetric (edges label)) :
    Symmetric (ClassRelation edges classes category) := by
  intro a b edge
  obtain ⟨label, selected, present⟩ := edge
  exact ⟨label, selected, each label selected a b present⟩

end TopOntoLogy
