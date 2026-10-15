"""ACE-Step ca1e85f LoRA loops; setup/checkpoint I/O omitted for GPU-free tests.

Source: https://github.com/ace-step/ACE-Step-1.5
MIT License
Copyright (c) 2026 ACEStep

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

class LoRATrainer:
    def _train_with_fabric(self, training_state):
        global_step = 0
        accumulation_step = 0
        accumulated_loss = 0.0
        ema_loss = None
        ema_alpha = 0.1
        start_epoch = 0
        for epoch in range(start_epoch, self.training_config.max_epochs):
            epoch_loss = 0.0
            num_updates = 0
            epoch_start_time = time.time()

            for _batch_idx, batch in enumerate(train_loader):
                # Check for stop signal
                if training_state and training_state.get("should_stop", False):
                    yield (
                        global_step,
                        accumulated_loss / max(accumulation_step, 1),
                        "⏹️ Training stopped by user",
                    )
                    return

                # Forward pass
                loss = self.module.training_step(batch)
                loss = loss / self.training_config.gradient_accumulation_steps

                # Backward pass
                self.fabric.backward(loss)
                accumulated_loss += loss.item()
                accumulation_step += 1

                # Optimizer step
                if (
                    accumulation_step
                    >= self.training_config.gradient_accumulation_steps
                ):
                    nonfinite_grads, grad_tensors = _count_nonfinite_grads(
                        trainable_params
                    )
                    if nonfinite_grads > 0:
                        optimizer.zero_grad(set_to_none=True)
                        yield (
                            global_step,
                            float("nan"),
                            (
                                f"⚠️ Non-finite gradients ({nonfinite_grads}/{grad_tensors}); "
                                "skipping optimizer step"
                            ),
                        )
                        accumulated_loss = 0.0
                        accumulation_step = 0
                        continue

                    self.fabric.clip_gradients(
                        self.module.model.decoder,
                        optimizer,
                        max_norm=self.training_config.max_grad_norm,
                        error_if_nonfinite=False,
                    )

                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)

                    global_step += 1

                    # Log
                    avg_loss = accumulated_loss / accumulation_step
                    if global_step % self.training_config.log_every_n_steps == 0:
                        if training_state is not None:
                            if ema_loss is None:
                                ema_loss = avg_loss
                            else:
                                ema_loss = (
                                    ema_alpha * avg_loss + (1 - ema_alpha) * ema_loss
                                )
                            training_state["plot_steps"].append(global_step)
                            training_state["plot_loss"].append(avg_loss)
                            training_state["plot_ema"].append(ema_loss)
                        self.fabric.log("train/loss", avg_loss, step=global_step)
                        self.fabric.log(
                            "train/lr", scheduler.get_last_lr()[0], step=global_step
                        )
                        yield (
                            global_step,
                            avg_loss,
                            f"Epoch {epoch + 1}/{self.training_config.max_epochs}, Step {global_step}, Loss: {avg_loss:.4f}",
                        )

                    epoch_loss += avg_loss
                    num_updates += 1
                    accumulated_loss = 0.0
                    accumulation_step = 0

            # Flush remainder to avoid dropping gradients when epoch length is not
            # divisible by gradient_accumulation_steps.
            if accumulation_step > 0:
                nonfinite_grads, grad_tensors = _count_nonfinite_grads(trainable_params)
                if nonfinite_grads > 0:
                    optimizer.zero_grad(set_to_none=True)
                    yield (
                        global_step,
                        float("nan"),
                        (
                            f"⚠️ Non-finite gradients ({nonfinite_grads}/{grad_tensors}); "
                            "skipping optimizer remainder step"
                        ),
                    )
                    accumulated_loss = 0.0
                    accumulation_step = 0
                else:
                    self.fabric.clip_gradients(
                        self.module.model.decoder,
                        optimizer,
                        max_norm=self.training_config.max_grad_norm,
                        error_if_nonfinite=False,
                    )

                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)

                global_step += 1
                avg_loss = accumulated_loss / accumulation_step
                if global_step % self.training_config.log_every_n_steps == 0:
                    if training_state is not None:
                        if ema_loss is None:
                            ema_loss = avg_loss
                        else:
                            ema_loss = ema_alpha * avg_loss + (1 - ema_alpha) * ema_loss
                        training_state["plot_steps"].append(global_step)
                        training_state["plot_loss"].append(avg_loss)
                        training_state["plot_ema"].append(ema_loss)
                    self.fabric.log("train/loss", avg_loss, step=global_step)
                    self.fabric.log(
                        "train/lr", scheduler.get_last_lr()[0], step=global_step
                    )
                    yield (
                        global_step,
                        avg_loss,
                        f"Epoch {epoch + 1}/{self.training_config.max_epochs}, Step {global_step}, Loss: {avg_loss:.4f}",
                    )

                    epoch_loss += avg_loss
                    num_updates += 1
                    accumulated_loss = 0.0
                    accumulation_step = 0

            # End of epoch

    def _train_basic(self, training_state):
        global_step = 0
        accumulation_step = 0
        accumulated_loss = 0.0
        ema_loss = None
        ema_alpha = 0.1
        start_epoch = 0
        for epoch in range(self.training_config.max_epochs):
            epoch_loss = 0.0
            num_updates = 0
            epoch_start_time = time.time()

            for batch in train_loader:
                if training_state and training_state.get("should_stop", False):
                    yield (
                        global_step,
                        accumulated_loss / max(accumulation_step, 1),
                        "⏹️ Training stopped",
                    )
                    return

                loss = self.module.training_step(batch)
                loss = loss / self.training_config.gradient_accumulation_steps
                loss.backward()
                accumulated_loss += loss.item()
                accumulation_step += 1

                if (
                    accumulation_step
                    >= self.training_config.gradient_accumulation_steps
                ):
                    torch.nn.utils.clip_grad_norm_(
                        trainable_params, self.training_config.max_grad_norm
                    )
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                    global_step += 1

                    avg_loss = accumulated_loss / accumulation_step
                    if global_step % self.training_config.log_every_n_steps == 0:
                        yield (
                            global_step,
                            avg_loss,
                            f"Epoch {epoch + 1}, Step {global_step}, Loss: {avg_loss:.4f}",
                        )

                    epoch_loss += avg_loss
                    num_updates += 1
                    accumulated_loss = 0.0
                    accumulation_step = 0

            if accumulation_step > 0:
                torch.nn.utils.clip_grad_norm_(
                    trainable_params, self.training_config.max_grad_norm
                )
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
                global_step += 1

                avg_loss = accumulated_loss / accumulation_step
                if global_step % self.training_config.log_every_n_steps == 0:
                    yield (
                        global_step,
                        avg_loss,
                        f"Epoch {epoch + 1}, Step {global_step}, Loss: {avg_loss:.4f}",
                    )

                epoch_loss += avg_loss
                num_updates += 1
                accumulated_loss = 0.0
                accumulation_step = 0
