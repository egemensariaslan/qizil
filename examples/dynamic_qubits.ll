; Dynamic qubit management: qubits come from the QIR runtime rather than the
; base profile's static addressing.  Runtime calls are opaque to Qizil and act
; as scheduling barriers, so no gate is ever moved across an allocate/release.
; ModuleID = 'dynamic_qubits'
source_filename = "dynamic_qubits"

%Qubit = type opaque
%Result = type opaque

define void @main() #0 {
entry:
  %q0 = call %Qubit* @__quantum__rt__qubit_allocate()
  %q1 = call %Qubit* @__quantum__rt__qubit_allocate()
  call void @__quantum__qis__h__body(%Qubit* %q0)
  call void @__quantum__qis__t__body(%Qubit* %q0)
  call void @__quantum__qis__t__body(%Qubit* %q0)
  call void @__quantum__qis__cnot__body(%Qubit* %q0, %Qubit* %q1)
  call void @__quantum__qis__cnot__body(%Qubit* %q0, %Qubit* %q1)
  call void @__quantum__qis__rz__body(double 0x3FD921FB54442D18, %Qubit* %q1)
  call void @__quantum__qis__rz__body(double 0x3FD921FB54442D18, %Qubit* %q1)
  call void @__quantum__qis__h__body(%Qubit* %q0)
  %r = call %Result* @__quantum__qis__m__body(%Qubit* %q0)
  call void @__quantum__rt__qubit_release(%Qubit* %q0)
  call void @__quantum__rt__qubit_release(%Qubit* %q1)
  ret void
}

declare %Qubit* @__quantum__rt__qubit_allocate()

declare void @__quantum__rt__qubit_release(%Qubit*)

declare void @__quantum__qis__h__body(%Qubit*)

declare void @__quantum__qis__t__body(%Qubit*)

declare void @__quantum__qis__rz__body(double, %Qubit*)

declare void @__quantum__qis__cnot__body(%Qubit*, %Qubit*)

declare %Result* @__quantum__qis__m__body(%Qubit*)

attributes #0 = { "entry_point" }
