"""Utility functions for Gaussian Processes.

"""
import os.path
import numpy as np
import gpytorch
import torch
import matplotlib.pyplot as plt
import casadi as ca

from copy import deepcopy

from safe_control_gym.utils.utils import mkdirs


def covSEard(x,
             z,
             ell,
             sf2
             ):
    """GP squared exponential kernel.

    This function is based on the 2018 GP-MPC library by Helge-André Langåker

    Args:
        x (np.array or casadi.MX/SX): First vector.
        z (np.array or casadi.MX/SX): Second vector.
        ell (np.array or casadi.MX/SX): Length scales.
        sf2 (float or casadi.MX/SX): output scale parameter.

    Returns:
        SE kernel (casadi.MX/SX): SE kernel.

    """
    dist = ca.sum1((x - z)**2 / ell**2)
    return sf2 * ca.SX.exp(-.5 * dist)


class ZeroMeanIndependentMultitaskGPModel(gpytorch.models.ExactGP):
    """Multidimensional Gaussian Process model with zero mean function.

    Or constant mean and radial basis function kernel (SE).

    """

    def __init__(self,
                 train_x,
                 train_y,
                 likelihood,
                 nx
                 ):
        """Initialize a multidimensional Gaussian Process model with zero mean function.

        Args:
            train_x (torch.Tensor): input training data (input_dim X N samples).
            train_y (torch.Tensor): output training data (output dim x N samples).
            likelihood (gpytorch.likelihood): Likelihood function (gpytorch.likelihoods.MultitaskGaussianLikelihood).
            nx (int): dimension of the target output (output dim)

        """
        super().__init__(train_x, train_y, likelihood)
        self.n = nx
        # For Zero mean function.
        self.mean_module = gpytorch.means.ZeroMean(
            batch_shape=torch.Size([self.n]))
        # For constant mean function.
        self.covar_module = gpytorch.kernels.ScaleKernel(
            gpytorch.kernels.RBFKernel(batch_shape=torch.Size([self.n]),
                                       ard_num_dims=train_x.shape[1]),
            batch_shape=torch.Size([self.n]),
            ard_num_dims=train_x.shape[1]
        )

    def forward(self,
                x
                ):
        """

        """
        mean_x = self.mean_module(x)
        covar_x = self.covar_module(x)
        return gpytorch.distributions.MultitaskMultivariateNormal.from_batch_mvn(
            gpytorch.distributions.MultivariateNormal(mean_x, covar_x)
        )


class ZeroMeanIndependentGPModel(gpytorch.models.ExactGP):
    """Single dimensional output Gaussian Process model with zero mean function.

    Or constant mean and radial basis function kernel (SE).

    """
    def __init__(self,
                 train_x,
                 train_y,
                 likelihood
                 ):
        """Initialize a single dimensional Gaussian Process model with zero mean function.

        Args:
            train_x (torch.Tensor): input training data (input_dim X N samples).
            train_y (torch.Tensor): output training data (output dim x N samples).
            likelihood (gpytorch.likelihood): Likelihood function (gpytorch.likelihoods.GaussianLikelihood).

        """
        super().__init__(train_x, train_y, likelihood)
        # For Zero mean function.
        self.mean_module = gpytorch.means.ZeroMean()
        # For constant mean function.
        self.covar_module = gpytorch.kernels.ScaleKernel(
            gpytorch.kernels.RBFKernel(ard_num_dims=train_x.shape[1]),
            ard_num_dims=train_x.shape[1]
        )

    def forward(self,
                x
                ):
        """

        """
        mean_x = self.mean_module(x)
        covar_x = self.covar_module(x)
        return gpytorch.distributions.MultivariateNormal(mean_x, covar_x)


class GaussianProcessCollection:
    """Collection of GaussianProcesses for multioutput GPs.

    """

    def __init__(self, model_type,
                 likelihood,
                 target_dim,
                 input_mask=None,
                 target_mask=None,
                 normalize=False
                 ):
        """Creates a single GaussianProcess for each output dimension.

        Args:
            model_type (gpytorch model class): Model class for the GP (ZeroMeanIndependentGPModel).
            likelihood (gpytorch.likelihood): likelihood function.
            target_dim (int): Dimension of the output (how many GPs to make).
            input_mask (list): Input dimensions to keep. If None, use all input dimensions.
            target_mask (list): Target dimensions to keep. If None, use all target dimensions.
            normalize (bool): If True, scale all data between -1 and 1.

        """
        self.gp_list = []
        self.model_type = model_type
        self.likelihood = likelihood
        self.optimizer = None
        self.model = None
        self.NORMALIZE = normalize
        self.input_mask = input_mask
        self.target_mask = target_mask
        for i in range(target_dim):
            self.gp_list.append(GaussianProcess(model_type,
                                                deepcopy(likelihood),
                                                input_mask=input_mask,
                                                normalize=normalize))

    def _init_properties(self,
                         train_inputs,
                         train_targets
                         ):
        """Initialize useful properties.

        Args:
            train_inputs, train_targets (torch.tensors): Input and target training data.

        """
        target_dimension = train_targets.shape[1]
        self.input_dimension = train_inputs.shape[1]
        self.output_dimension = target_dimension
        self.n_training_samples = train_inputs.shape[0]

    def init_with_hyperparam(self,
                             train_inputs,
                             train_targets,
                             path_to_statedicts
                             ):
        """Load hyperparameters from a state_dict.

        Args:
            train_inputs, train_targets (torch.tensors): Input and target training data.
            path_to_statedicts (str): Path to where the state dicts are saved.

        """
        self._init_properties(train_inputs, train_targets)
        target_dimension = train_targets.shape[1]
        gp_K_plus_noise_list = []
        for gp_ind, gp in enumerate(self.gp_list):
            path = os.path.join(path_to_statedicts, 'best_model_%s.pth'  % gp_ind)
            print("#########################################")
            print("#       Loading GP dimension %s         #" % gp_ind)
            print("#########################################")
            print('Path: %s' % path)
            gp.init_with_hyperparam(train_inputs,
                                    train_targets[:,gp_ind],
                                    path)
            gp_K_plus_noise_list.append(gp.model.K_plus_noise.detach())
            print('Loaded!')
        gp_K_plus_noise = torch.stack(gp_K_plus_noise_list)
        self.K_plus_noise = gp_K_plus_noise

    def get_hyperparameters(self,
                            as_numpy=False
                            ):
        """Get the outputscale and lengthscale from the kernel matrices of the GPs.

        """
        lengthscale_list = []
        output_scale_list = []
        noise_list = []
        for gp in self.gp_list:
            lengthscale_list.append(gp.model.covar_module.base_kernel.lengthscale.detach())
            output_scale_list.append(gp.model.covar_module.outputscale.detach())
            noise_list.append(gp.model.likelihood.noise.detach())
        lengthscale = torch.cat(lengthscale_list)
        outputscale = torch.Tensor(output_scale_list)
        noise = torch.Tensor(noise_list)
        if as_numpy:
            return lengthscale.numpy(), outputscale.numpy(), noise.numpy(), self.K_plus_noise.detach().numpy()
        else:
            return lengthscale, outputscale, noise, self.K_plus_noise

    def train(self,
              train_x_raw,
              train_y_raw,
              n_train=[500],
              learning_rate=[0.01],
              gpu=False,
              dir='results'
              ):
        """Train the GP using Train_x and Train_y.

        Args:
            train_x: Torch tensor (N samples [rows] by input dim [cols])
            train_y: Torch tensor (N samples [rows] by target dim [cols])

        """
        self._init_properties(train_x_raw, train_y_raw)
        self.model_paths = []
        mkdirs(dir)
        for gp_ind, gp in enumerate(self.gp_list):
            lr = learning_rate[self.target_mask[gp_ind]]
            n_t = n_train[self.target_mask[gp_ind]]
            print("#########################################")
            print("#      Training GP dimension %s         #" % gp_ind)
            print("#########################################")
            print("Train iterations: %s" % n_t)
            print("Learning Rate:: %s" % lr)
            gp_K_plus_noise_list = []
            gp.train(train_x_raw,
                     train_y_raw[:,gp_ind],
                     n_train=n_t,
                     learning_rate=lr,
                     gpu=gpu,
                     fname=os.path.join(dir, 'best_model_%s.pth' % gp_ind))
            self.model_paths.append(dir)
            gp_K_plus_noise_list.append(gp.model.K_plus_noise)
        gp_K_plus_noise = torch.stack(gp_K_plus_noise_list)
        self.K_plus_noise = gp_K_plus_noise

    def predict(self,
                x,
                requires_grad=False,
                return_pred=True
                ):
        """

        Args:
            x : torch.Tensor (N_samples x input DIM).

        Return
            Predictions
                mean : torch.tensor (nx X N_samples).
                lower : torch.tensor (nx X N_samples).
                upper : torch.tensor (nx X N_samples).

        """
        means_list = []
        cov_list = []
        pred_list = []
        for gp in self.gp_list:
            if return_pred:
                mean, cov, pred = gp.predict(x, requires_grad=requires_grad, return_pred=return_pred)
                pred_list.append(pred)
            else:
                mean, cov = gp.predict(x, requires_grad=requires_grad, return_pred=return_pred)
            means_list.append(mean)
            cov_list.append(cov)
        means = torch.tensor(means_list)
        cov = torch.diag(torch.cat(cov_list).squeeze())
        if return_pred:
            return means, cov, pred_list
        else:
            return means, cov

    def prediction_jacobian(self,
                            query
                            ):
        """Return Jacobian.

        """
        return NotImplementedError

    def plot_trained_gp(self,
                        inputs,
                        targets,
                        fig_count=0
                        ):
        """Plot the trained GP given the input and target data.

        """
        for gp_ind, gp in enumerate(self.gp_list):
            fig_count = gp.plot_trained_gp(inputs, targets[:,gp_ind,None], fig_count)
            fig_count += 1

    def _kernel_list(self,
                     x1,
                     x2=None
                     ):
        """Evaluate the kernel given vectors x1 and x2.

        Args:
            x1 (torch.Tensor): First vector.
            x2 (torch.Tensor): Second vector.

        Returns:
            list of LazyTensor Kernels.

        """
        if x2 is None:
            x2 = x1
        k_list = []
        for gp in self.gp_list:
            k_list.append(gp.model.covar_module(x1, x2))
        return k_list

    def kernel(self,
               x1,
               x2=None
               ):
        """Evaluate the kernel given vectors x1 and x2.

        Args:
            x1 (torch.Tensor): First vector.
            x2 (torch.Tensor): Second vector.

        Returns:
            Torch tensor of the non-lazy kernel matrices.

        """
        k_list = self._kernel_list(x1, x2)
        non_lazy_tensors = [k.evaluate() for k in k_list]
        return torch.stack(non_lazy_tensors)

    def kernel_inv(self,
                   x1,
                   x2=None
                   ):
        """Evaluate the inverse kernel given vectors x1 and x2.

        Only works for square kernel.

        Args:
            x1 (torch.Tensor): First vector.
            x2 (torch.Tensor): Second vector.

        Returns:
            Torch tensor of the non-lazy inverse kernel matrices.

        """
        if x2 is None:
            x2 = x1
        assert x1.shape == x2.shape, ValueError("x1 and x2 need to have the same shape.")
        k_list = self._kernel_list(x1, x2)
        num_of_points = x1.shape[0]
        # Efficient inversion is performed VIA inv_matmul on the laze tensor with Identity.
        non_lazy_tensors = [k.inv_matmul(torch.eye(num_of_points).double()) for k in k_list]
        return torch.stack(non_lazy_tensors)

class GaussianProcess:
    """Gaussian Process decorator for gpytorch.

    """

    def __init__(self,
                  model_type,
                  likelihood,
                  input_mask=None,
                  target_mask=None,
                  normalize=False
                  ):
        """Initialize Gaussian Process.
       
        Args:
            model_type (gpytorch model class): Model class for the GP (ZeroMeanIndependentMultitaskGPModel).
            likelihood (gpytorch.likelihood): likelihood function.
            normalize (bool): If True, scale all data between -1 and 1. (prototype and not fully operational).

        """
        self.model_type = model_type
        self.likelihood = likelihood
        self.optimizer = None
        self.model = None
        self.NORMALIZE = normalize
        self.input_mask = input_mask
        self.target_mask = target_mask

    def _init_model(self,
                    train_inputs,
                    train_targets
                    ):
        """Init GP model from train inputs and train_targets.

        """
        if train_targets.ndim > 1:
            target_dimension = train_targets.shape[1]
        else:
            target_dimension = 1
        if self.model is None:
            self.model = self.model_type(train_inputs,
                                         train_targets,
                                         self.likelihood)
        # Extract dimensions for external use.
        self.input_dimension = train_inputs.shape[1]
        self.output_dimension = target_dimension
        self.n_training_samples = train_inputs.shape[0]

    def _compute_GP_covariances(self,
                                train_x
                                ):
        """Compute K(X,X) + sigma*I and its inverse.

        """
        # Pre-compute inverse covariance plus noise to speed-up computation.
        K_lazy = self.model.covar_module(train_x.double())
        K_lazy_plus_noise = K_lazy.add_diag(self.model.likelihood.noise)
        n_samples = train_x.shape[0]
        self.model.K_plus_noise = K_lazy_plus_noise.matmul(torch.eye(n_samples).double())
        self.model.K_plus_noise_inv = K_lazy_plus_noise.inv_matmul(torch.eye(n_samples).double())

    def init_with_hyperparam(self,
                             train_inputs,
                             train_targets,
                             path_to_statedict
                             ):
        """Load hyperparameters from a state_dict.

        """
        if self.input_mask is not None:
            train_inputs = train_inputs[:, self.input_mask]
        if self.target_mask is not None:
            train_targets = train_targets[:, self.target_mask]
        device = torch.device('cpu')
        state_dict = torch.load(path_to_statedict, map_location=device)
        self._init_model(train_inputs, train_targets)
        self.model.load_state_dict(state_dict)
        self.model.double() # needed otherwise loads state_dict as float32
        self._compute_GP_covariances(train_inputs)

    def train(self,
              train_x_raw,
              train_y_raw,
              n_train=500,
              learning_rate=0.01,
              gpu=False,
              fname='best_model.pth'
              ):
        """Train the GP using Train_x and Train_y.

        Args:
            train_x: Torch tensor (N samples [rows] by input dim [cols])
            train_y: Torch tensor (N samples [rows] by target dim [cols])

        """
        if self.input_mask is not None:
            train_x_raw = train_x_raw[:, self.input_mask]
        if self.target_mask is not None:
            train_y_raw = train_y_raw[:, self.target_mask]
        if self.NORMALIZE:
            self.scale_normalization = 2/(train_x_raw.max(0)[0] - train_x_raw.min(0)[0])
            self.scale_shift = (-train_x_raw.max(0)[0] - train_x_raw.min(0)[0])/(train_x_raw.max(0)[0] -
                                                                                 train_x_raw.min(0)[0])
            train_x = self.normalize(train_x_raw)
            train_y = train_y_raw
        else:
            train_x = train_x_raw
            train_y = train_y_raw
        self._init_model(train_x, train_y)
        if gpu:
            train_x = train_x.cuda()
            train_y = train_y.cuda()
            self.model = self.model.cuda()
            self.likelihood = self.likelihood.cuda()
        self.model.double()
        self.likelihood.double()
        self.model.train()
        self.likelihood.train()
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=learning_rate)
        mll = gpytorch.mlls.ExactMarginalLogLikelihood(self.likelihood, self.model)
        last_loss = 99999999
        best_loss = 99999999
        loss = torch.tensor(0)
        i = 0
        while i < n_train and torch.abs(loss - last_loss) > 1e-2:
            self.optimizer.zero_grad()
            output = self.model(train_x)
            loss = -mll(output, train_y)
            loss.backward()
            print('Iter %d/%d - Loss: %.3f' % (i + 1, n_train, loss.item()))
            self.optimizer.step()
            if loss < best_loss:
                best_loss = loss
                state_dict = self.model.state_dict()
                torch.save(state_dict, fname)
                best_epoch = i

            i+=1
        print("Training Complete")
        print("Lowest epoch: %s" %best_epoch)
        print("Lowest Loss: %s" % best_loss)
        self.model = self.model.cpu()
        self.likelihood = self.likelihood.cpu()
        train_x = train_x.cpu()
        train_y = train_y.cpu()
        self.model.load_state_dict(torch.load(fname))
        self._compute_GP_covariances(train_x)

    def predict(self,
                x,
                requires_grad=False,
                return_pred=True
                ):
        """

        Args:
            x : torch.Tensor (N_samples x input DIM).

        Returns:
            Predictions
                mean : torch.tensor (nx X N_samples).
                lower : torch.tensor (nx X N_samples).
                upper : torch.tensor (nx X N_samples).

        """
        self.model.eval()
        self.likelihood.eval()
        if type(x) is np.ndarray:
            x = torch.from_numpy(x).double()
        if self.input_mask is not None:
            x = x[:,self.input_mask]
        if self.NORMALIZE:
            x = self.normalize(x)
        if requires_grad:
            predictions = self.likelihood(self.model(x))
            mean = predictions.mean
            cov = predictions.covariance_matrix
        else:
            with torch.no_grad(), gpytorch.settings.fast_pred_var(state=False):
                predictions = self.likelihood(self.model(x))
                mean = predictions.mean
                cov = predictions.covariance_matrix
        if return_pred:
            return mean, cov, predictions
        else:
            return mean, cov

    def prediction_jacobian(self,
                            query
                            ):
        mean_der, cov_der = torch.autograd.functional.jacobian(
                                lambda x: self.predict(x, requires_grad=True, return_pred=False),
                                query.double())
        return mean_der.detach().squeeze()

    def plot_trained_gp(self,
                        inputs,
                        targets,
                        fig_count=0
                        ):
        if self.target_mask is not None:
            targets = targets[:, self.target_mask]
        means, covs, preds = self.predict(inputs)
        t = np.arange(inputs.shape[0])
        lower, upper = preds.confidence_region()
        for i in range(self.output_dimension):
            fig_count += 1
            plt.figure(fig_count)
            if lower.ndim > 1:
                plt.fill_between(t, lower[:,i].detach().numpy(), upper[:,i].detach().numpy(), alpha=0.5, label='95%')
                plt.plot(t, means[:, i], 'r', label='GP Mean')
                plt.plot(t, targets[:, i], '*k', label='Data')
            else:
                plt.fill_between(t, lower.detach().numpy(), upper.detach().numpy(), alpha=0.5, label='95%')
                plt.plot(t, means, 'r', label='GP Mean')
                plt.plot(t, targets, '*k', label='Data')
            plt.legend()
            plt.title('Fitted GP x%s' % i)
            plt.xlabel('Time (s)')
            plt.ylabel('v')
            plt.show()
        return fig_count

    def normalize(self,
                  vector
                  ):
        dim = vector.shape[1]
        return normalize(vector,
                         self.scale_normalization[:dim],
                         self.scale_shift[:dim])

    def unnormalize(self,
                    vector
                    ):
        dim = vector.shape[1]
        return unnormalize(vector,
                           self.scale_normalization[:dim],
                           self.scale_shift[:dim])


def normalize(vector,
              a,
              b
              ):
    return vector*a + b


def unnormalize(vector,
                a,
                b
                ):
    return (vector - b)/a
