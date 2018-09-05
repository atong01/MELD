import numpy as np
import pygsp
import graphtools
import graphtools.base
import scipy.sparse as sparse
import inspect
from sklearn.cluster import KMeans
from . import utils


def meld(X, gamma, g, offset = 0, order = 1, solver='cheby', fi='regularizedlaplacian', alpha=2):
    """
    Performs convex meld on the input signal.
    This function solves:

        (1) :math:`sol = argmin_{z} \frac{1}{2}\|x - z\|_2^2 + \gamma \| \nabla x\|_2^2`

        OR
        regularizes (1) using \inner{X, randomwalk(L)*X}, the p-step random walk (math to come)

    Note the nice following relationship for (1):

        (2) :math:`x^T L x = \| \nabla x\|_2^2`

    Also note that the solution to (1) may be phrased as the lowpass filter:

        (3) :math:`sol = h(L)x` with :math:`h(\lambda) := \frac{1}{1+\gamma\lambda}`

    We use (3) by default as it is faster in the case of few input signals.


    Parameters
    ----------
    X : ndarray [n, p]
        2 dimensional input signal array to meld.
    gamma : int
        Amount of smoothing to apply.  Acts as 'p' parameter if fi == 'randomwalk'
    g : graphtools.Graph object
        Graph to perform data smoothing over.
    offset: int, optional, Default: 0
        Amount to shift the MELD filter in the eigenvalue spectrum.  Recommend using
        an eigenvalue from g based on the spectral distribution.
    order: int, optional, Default: 1
        Falloff and smoothness of the filter.   High order leads to square-like filters.
    Solver : string, optional, Default: 'cheby'
        Method to solve convex problem.
        'cheby' uses a chebyshev polynomial approximation of the corresponding filter
        'matrix' solves the convex problem exactly
    fi: string, optional, Default: 'regularizedlaplacian'
        Filter to use for (1).
        'regularizedlaplacian' is the exact solution of (1)
        'randomwalk' is a randomwalk polynomial that is related to diffusion via rw = ((alpha-1)I+P)^t
    
    Returns
    -------
    sol : ndarray [n, p]
        2 dimensional array of smoothed input signals
    """

    if not isinstance(solver, str):
        raise TypeError("Input method should be a string")
    solver = solver.lower()
    if solver not in ['matrix', 'cheby']:
        raise NotImplementedError(
            '{} solver is not currently implemented.'.format(solver))

    if not isinstance(fi, str):
        raise TypeError("Input filter should be a string")
    fi = fi.lower()
    if fi not in ['regularizedlaplacian', 'randomwalk']:
        raise NotImplementedError(
            '{} filter is not currently implemented.'.format(fi))

    if not isinstance(g, pygsp.graphs.Graph):
        if isinstance(g, graphtools.base.BaseGraph):
            raise TypeError(
                "Input graph should be of type pygsp.graphs.Graph. "
                "When using graphtools, use the `use_pygsp=True` flag.")
        else:
            raise TypeError(
                "Input graph should be of type pygsp.graphs.Graph. "
                "Got {}".format(type(g)))

    if X.shape[0] != g.N:
        if len(X.shape) > 1 and X.shape[1] == g.N:
            print(
                "input matrix is column-wise rather than row-wise. "
                "transposing (output will be transposed)")
            X = X.T
        else:
            raise ValueError(
                "Input data ({}) and input graph ({}) "
                "are not of the same size".format(X.shape, g.N))

    if fi == 'randomwalk':
        # used for random walk stochasticity
        D = sparse.diags(np.ravel(np.power(g.W.sum(0), -1)), 0).tocsc()
    if solver == 'matrix':
        # use matrix inversion / powering
        I = sparse.identity(g.N)
        if fi == 'regularizedlaplacian':  # fTLf
            mat = sparse.linalg.inv((I + np.matrix_power(gamma * g.L-offset*I, order)).tocsc())
        elif fi == 'randomwalk':  # p-step random walk
            mat = (alpha * I - (g.L * D))**gamma

        sol = mat.T @ X  # apply the matrix
        sol = np.squeeze(np.asarray(sol))  # deliver a vector

    else:
        # use approximations
        if fi == 'regularizedlaplacian':  # fTLf
            filterfunc = lambda x: 1 / (1 + (gamma * x-offset)**order)

        elif fi == 'randomwalk':  # p-step random walk
            L_bak = g.L
            # change the eigenbasis by normalizing by degree (stochasticity)
            g.L = (L_bak * D).T
            filterfunc = lambda x: (alpha - x)**gamma

        g.estimate_lmax()
        filt = pygsp.filters.Filter(g, filterfunc)  # build filter
        sol = filt.filter(X)  # apply filter
        if fi == 'randomwalk':
            g.L = L_bak  # restore L

    sol = utils.convert_to_same_format(sol, X)

    return sol


def spectrogram_clustering(g, s = None,  t = 10, saturation = 0.5, kernel = None, clusterobj = None, nclusts = 5, precomputed_nwgft = None, **kwargs):
    saturation_func = lambda x,alpha: np.tanh(alpha * np.abs(x.T)) #TODO: extend to allow different saturation functions
    if not(isinstance(clusterobj, KMeans)):
        #todo: add support for other clustering algorithms
        if clusterobj is None:
            clusterobj = KMeans(n_clusters = nclusts, **kwargs)
        else:
            raise TypeError(
                "Currently only sklearn.cluster.KMeans is supported for clustering object. "
                "Got {}".format(type(clusterobj)))
            
    if precomputed_nwgft is not None: #we don't need to do much if we have a precomputed nwgft
        C = precomputed_nwgft
    else:
        #check that signal and graph are defined
        if s is None:
            raise RuntimeError(
                    "If no precomputed_nwgft, then a signal s should be supplied.")
        if not isinstance(g, pygsp.graphs.Graph):
            if isinstance(g, graphtools.base.BaseGraph):
                raise TypeError(
                    "Input graph should be of type pygsp.graphs.Graph. "
                    "When using graphtools, use the `use_pygsp=True` flag.")
            else:
                raise TypeError(
                    "Input graph should be of type pygsp.graphs.Graph. "
                    "Got {}".format(type(g)))
        #build kernel
        if kernel and not(inspect.isfunction(kernel)):
                raise TypeError(
                    "Input kernel should be a lambda function (accepting "
                    "eigenvalues of the graph laplacian) or none. "
                    "Got {}".format(type(kernel)))
        if kernel is None:  
            kernel = lambda x:  np.exp((-t*x)/g.lmax) #definition of the heat kernel
        
        ke = kernel(g.e) #eval kernel over eigenvalues of G
        ktrans = np.sqrt(g.N) * (g.U @ np.multiply(ke[:,None],g.U.T)) #vertex domain translation of the kernel. 
        
        C = np.empty((g.N,g.N))
        
        for i in range(0,g.N): #build frame matrix
            kmod = np.matlib.repmat(ktrans[:,i], 1,g.N) # copy one translate Ntimes
            kmod = np.reshape(kmod,(g.N,g.N)).T 
            kmod = (g.U/g.U[:,0]) * kmod # modulate the copy at each frequency of G 
            kmod = kmod / np.linalg.norm(kmod,axis = 0) #normalize it
            C[:,i] = kmod.T@s # compute nwgft frame
    
    labels = clusterobj.fit_predict(saturation_func(C,saturation))
    
    return C, labels, saturation_func
