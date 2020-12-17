import numpy as np


def you2kappa(eta, chi, phi, kappa_angle=60, degrees=True, bernina_kappa=True):
    """tool to convert from you definition angles to kappa angles, in
    particular the bernina kappa where the"""
    if degrees:
        eta, chi, phi, kappa_angle = np.deg2rad([eta, chi, phi, kappa_angle])
    delta_angle = np.arcsin(-np.tan(chi / 2) / np.tan(kappa_angle))
    eta_k = eta - delta_angle
    kappa = 2 * np.arcsin(np.sin(chi / 2) / np.sin(kappa_angle))
    phi_k = phi - delta_angle
    
    if bernina_kappa:
        eta_k = eta_k - np.pi/2
        phi_k = -phi_k + np.pi/2
    if degrees:
        eta_k, kappa, phi_k = np.rad2deg([eta_k, kappa, phi_k])
    return eta_k, kappa, phi_k


def kappa2you(eta_k, kappa, phi_k, kappa_angle=60, degrees=True, bernina_kappa=True):
    if degrees:
        eta_k, kappa, phi_k, kappa_angle = np.deg2rad(
            [eta_k, kappa, phi_k, kappa_angle]
        )
    if bernina_kappa:    
        eta_k = eta_k + np.pi / 2 
        phi_k = -phi_k + np.pi / 2 
    delta_angle = np.arctan(np.tan(kappa / 2) * np.cos(kappa_angle))
    eta = eta_k - delta_angle
    chi = 2 * np.arcsin(np.sin(kappa / 2) * np.sin(kappa_angle))
    phi = phi_k - delta_angle
    if degrees:
        eta, chi, phi = np.rad2deg([eta, chi, phi])
    return eta, chi, phi
