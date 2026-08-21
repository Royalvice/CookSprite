"""NormalCrafter's temporal-image-conditioning UNet adapter.

Derived from `normalcrafter/unet.py` in Binyr/NormalCrafter (MIT).  The
upstream checkpoint conditions every video frame on its own CLIP embedding;
the stock Diffusers SVD UNet repeats a single embedding for all frames.  This
small forward override preserves the upstream frame-wise conditioning while
leaving the released Diffusers module topology and weights intact.
"""

from __future__ import annotations

import torch
from diffusers import UNetSpatioTemporalConditionModel
from diffusers.models.unets.unet_spatio_temporal_condition import (
    UNetSpatioTemporalConditionOutput,
)


class NormalCrafterUNet(UNetSpatioTemporalConditionModel):
    """SVD UNet accepting one image embedding per temporal frame."""

    def forward(
        self,
        sample: torch.Tensor,
        timestep: torch.Tensor | float,
        encoder_hidden_states: torch.Tensor,
        added_time_ids: torch.Tensor,
        return_dict: bool = True,
    ) -> UNetSpatioTemporalConditionOutput | tuple[torch.Tensor]:
        default_overall_up_factor = 2**self.num_upsamplers
        forward_upsample_size = any(
            size % default_overall_up_factor != 0 for size in sample.shape[-2:]
        )
        upsample_size = None

        if not torch.is_tensor(timestep):
            dtype = torch.float32 if sample.device.type in {"mps", "npu"} else torch.float64
            if not isinstance(timestep, float):
                dtype = torch.int32 if sample.device.type in {"mps", "npu"} else torch.int64
            timesteps = torch.tensor([timestep], dtype=dtype, device=sample.device)
        elif timestep.ndim == 0:
            timesteps = timestep[None].to(sample.device)
        else:
            timesteps = timestep.to(sample.device)

        batch_size, num_frames = sample.shape[:2]
        timesteps = timesteps.expand(batch_size)
        time_embedding = self.time_proj(timesteps).to(dtype=sample.dtype)
        embedding = self.time_embedding(time_embedding)
        additional = self.add_time_proj(added_time_ids.flatten()).reshape((batch_size, -1))
        additional = self.add_embedding(additional.to(embedding.dtype))

        # This is the meaningful NormalCrafter difference from stock SVD: a
        # (frames, tokens, dim) CLIP tensor stays frame-indexed rather than
        # being repeated frames times again.
        if embedding.shape[0] == 1:
            embedding = (embedding + additional).repeat_interleave(num_frames, dim=0)
        else:
            embedding = embedding + additional.repeat_interleave(num_frames, dim=0)

        sample = sample.flatten(0, 1)
        if sample.shape[0] != encoder_hidden_states.shape[0]:
            encoder_hidden_states = encoder_hidden_states.repeat_interleave(num_frames, dim=0)
        sample = self.conv_in(sample)
        image_only_indicator = torch.zeros(
            batch_size, num_frames, dtype=sample.dtype, device=sample.device
        )

        down_block_res_samples = (sample,)
        for downsample_block in self.down_blocks:
            if getattr(downsample_block, "has_cross_attention", False):
                sample, residuals = downsample_block(
                    hidden_states=sample,
                    temb=embedding,
                    encoder_hidden_states=encoder_hidden_states,
                    image_only_indicator=image_only_indicator,
                )
            else:
                sample, residuals = downsample_block(
                    hidden_states=sample,
                    temb=embedding,
                    image_only_indicator=image_only_indicator,
                )
            down_block_res_samples += residuals

        sample = self.mid_block(
            hidden_states=sample,
            temb=embedding,
            encoder_hidden_states=encoder_hidden_states,
            image_only_indicator=image_only_indicator,
        )

        for index, upsample_block in enumerate(self.up_blocks):
            is_final = index == len(self.up_blocks) - 1
            residuals = down_block_res_samples[-len(upsample_block.resnets) :]
            down_block_res_samples = down_block_res_samples[: -len(upsample_block.resnets)]
            if not is_final and forward_upsample_size:
                upsample_size = down_block_res_samples[-1].shape[2:]
            if getattr(upsample_block, "has_cross_attention", False):
                sample = upsample_block(
                    hidden_states=sample,
                    temb=embedding,
                    res_hidden_states_tuple=residuals,
                    encoder_hidden_states=encoder_hidden_states,
                    upsample_size=upsample_size,
                    image_only_indicator=image_only_indicator,
                )
            else:
                sample = upsample_block(
                    hidden_states=sample,
                    temb=embedding,
                    res_hidden_states_tuple=residuals,
                    upsample_size=upsample_size,
                    image_only_indicator=image_only_indicator,
                )

        sample = self.conv_out(self.conv_act(self.conv_norm_out(sample)))
        sample = sample.reshape(batch_size, num_frames, *sample.shape[1:])
        if not return_dict:
            return (sample,)
        return UNetSpatioTemporalConditionOutput(sample=sample)


__all__ = ["NormalCrafterUNet"]
