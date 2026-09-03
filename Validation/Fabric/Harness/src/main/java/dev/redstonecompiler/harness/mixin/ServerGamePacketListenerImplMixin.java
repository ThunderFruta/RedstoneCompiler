package dev.redstonecompiler.harness.mixin;

import net.minecraft.server.network.ServerGamePacketListenerImpl;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Constant;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.ModifyConstant;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Disables vanilla speed-based position corrections on the private compiler server. */
@Mixin(ServerGamePacketListenerImpl.class)
abstract class ServerGamePacketListenerImplMixin {
    @Inject(
            method = "shouldCheckPlayerMovement",
            at = @At("HEAD"),
            cancellable = true)
    private void RedstoneCompiler$DisablePlayerSpeedCheck(
            boolean IsFallFlying,
            CallbackInfoReturnable<Boolean> Callback) {
        Callback.setReturnValue(false);
    }

    @ModifyConstant(
            method = "handleMovePlayer",
            constant = @Constant(doubleValue = 0.0625D))
    private double RedstoneCompiler$DisablePlayerWrongMoveThreshold(
            double Threshold) {
        return Double.MAX_VALUE;
    }

    @ModifyConstant(
            method = "handleMoveVehicle",
            constant = @Constant(doubleValue = 100.0D))
    private double RedstoneCompiler$DisableVehicleSpeedThreshold(
            double Threshold) {
        return Double.MAX_VALUE;
    }

    @ModifyConstant(
            method = "handleMoveVehicle",
            constant = @Constant(doubleValue = 0.0625D))
    private double RedstoneCompiler$DisableVehicleWrongMoveThreshold(
            double Threshold) {
        return Double.MAX_VALUE;
    }
}
